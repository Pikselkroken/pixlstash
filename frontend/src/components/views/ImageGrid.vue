<template>
  <ImageOverlay
    ref="imageOverlayRef"
    :open="overlayOpen"
    :initialImageId="overlayImageId"
    :initialExpandedStackIds="overlayInitialExpandedStackIds"
    :allImages="allGridImages"
    :tagUpdate="wsStore.wsTagUpdate"
    :descriptionUpdate="wsStore.wsDescriptionUpdate"
    :smartScoreUpdate="wsStore.wsSmartScoreUpdate"
    :detectionUpdate="wsStore.wsDetectionUpdate"
    :hiddenTags="userPrefsStore.hiddenTags"
    :applyTagFilter="userPrefsStore.applyTagFilter"
    :dateFormat="userPrefsStore.dateFormat"
    :showStacks="gridStore.showStacks"
    :showProblemIcon="gridStore.showProblemIcon"
    :availablePlugins="availablePlugins"
    :comfyuiProgress="comfyuiProgress"
    :comfyuiProgressPercent="comfyuiProgressPercent"
    :pluginProgress="pluginProgress"
    :pluginProgressPercent="pluginProgressPercent"
    :comfyuiClientId="comfyuiClientId"
    :comfyuiConfigured="filterStore.comfyuiConfigured"
    :guestScore="overlayGuestScore"
    @close="closeOverlay"
    @apply-score="applyScore"
    @set-guest-score="(img, n) => setGuestScore(img, n)"
    @add-tag="addTagToImage"
    @update-description="updateDescriptionForImage"
    @overlay-change="handleOverlayChange"
    @added-to-set="handleOverlayAddedToSet"
    @set-project="handleSetProjectForSelected"
    @comfyui-run="handleComfyuiRun"
    @run-plugin="handlePluginRunRequest"
    @request-context-menu="handleOverlayContextMenuRequest"
    @character-created="emit('refresh-sidebar')"
  />
  <ImageImporter
    ref="imageImporterRef"
    @import-started="handleImportStarted"
    @import-finished="handleImagesUploaded"
    @import-cancelled="handleImportCancelled"
    @import-error="handleImportErrored"
  />
  <div :style="wrapperStyle" class="grid-content-area">
    <Toolbar
      :selectedCount="selectedImageIds.length"
      :selectedCharacter="String(selectionStore.selectedCharacter)"
      :selectedSort="sortStore.selectedSort"
      :allPicturesId="String(ALL_PICTURES_ID)"
      :comfyui-configured="filterStore.comfyuiConfigured"
      @comfyui-run-grid="runComfyuiOnGridImages"
      @expand-all-stacks="expandAllStacks"
      @collapse-all-stacks="collapseAllStacks"
      @open-duplicates="emit('open-duplicates')"
      @open-settings="emit('open-settings')"
      @open-import="emit('open-import')"
      @local-import="emit('local-import', $event)"
      @confirm-export-zip="emit('confirm-export-zip')"
      @confirm-export-folder="emit('confirm-export-folder', $event)"
    />
    <!-- ── Visible range pill ── -->
    <transition name="grid-range-fade">
      <span v-if="visibleRangeLabel" class="grid-range-pill">{{
        visibleRangeLabel
      }}</span>
    </transition>
    <!-- ── Breadcrumb: current-view path (bottom-left overlay) ──
         Hidden on the desktop shell, where it lives in the title bar. -->
    <nav
      v-if="breadcrumb.length && !isDesktop"
      ref="breadcrumbEl"
      class="grid-breadcrumb"
      :class="{
        'grid-breadcrumb--above-bar': isMultiCharacterView || isSetOverlapView,
      }"
      aria-label="Current view"
    >
      <template v-for="(crumb, i) in breadcrumb" :key="i">
        <span v-if="i > 0" class="grid-breadcrumb-sep" aria-hidden="true"
          >›</span
        >
        <button
          v-if="crumb.to"
          type="button"
          class="grid-breadcrumb-crumb is-link"
          :title="`Go to ${crumb.label}`"
          @click="navigateBreadcrumb(crumb)"
        >
          {{ crumb.label }}
        </button>
        <span v-else class="grid-breadcrumb-crumb" :title="crumb.label">{{
          crumb.label
        }}</span>
      </template>
    </nav>
    <ImageGridContextMenu
      :visible="contextMenuVisible"
      :x="contextMenuX"
      :y="contextMenuY"
      :selected-image-ids="selectedImageIds"
      :selected-media-support="selectedMediaSupport"
      :selected-character="String(selectionStore.selectedCharacter)"
      :selected-group-name="selectedGroupName"
      :selected-sort="sortStore.selectedSort"
      :scrapheap-pictures-id="String(SCRAPHEAP_PICTURES_ID)"
      :comfyui-configured="filterStore.comfyuiConfigured"
      :show-remove-from-stack="showRemoveFromStack"
      :selected-multiple-stack-ids="selectedMultipleStackIds"
      :keep-cover-only-stack-count="keepCoverOnlyStackCount"
      :keep-cover-only-lock-reason="keepCoverOnlyLockReason"
      :grouping-lock-reason="partialStackGroupingReason"
      :rotate-block-reason="selectionRotateBlockReason"
      :lock-reason="selectionLockReason"
      :locked-set-ids="lockedSetsStore.lockedSetIds"
      :available-plugins="availablePlugins"
      :tagger-plugins="taggerPlugins"
      :captioner-plugins="captionerPlugins"
      :context-image="contextMenuImage"
      :context-clicked-face="contextMenuClickedFace"
      :is-shared="
        contextMenuImage ? sharedPictureIds.has(contextMenuImage.id) : false
      "
      @close="contextMenuVisible = false"
      @added-to-set="handleOverlayAddedToSet"
      @add-to-character="handleAddToCharacter"
      @remove-from-character="handleRemoveFromCharacter"
      @create-character="handleCreateCharacterFromMenu"
      @set-project="handleSetProjectForSelected"
      @remove-from-stack="removeSelectedFromStack"
      @dissolve-stacks="dissolveSelectedStacks"
      @create-stack="createStackFromSelection"
      @create-stacks-from-groups="createStacksFromSelectedGroups"
      @remove-from-group="removeFromGroup"
      @keep-cover-only="openKeepCoverOnly"
      @delete-selected="deleteSelected"
      @open-tag-panel="handleContextMenuOpenTagPanel"
      @open-plugin-panel="handleContextMenuOpenPluginPanel"
      @open-comfyui-panel="handleContextMenuOpenComfyuiPanel"
      @open-remix-dialog="openRemixDialog"
      @segment="openSegmentDialog"
      @auto-tag="handleAutoTag"
      @generate-description="handleGenerateDescription"
      @share-picture="sharePicture"
      @remove-picture-shares="openRevokeSharesDialog"
      @reverse-image-search="handleReverseImageSearch"
      @find-similar-faces="handleFindSimilarFaces"
      @rotate-left="rotateSelectedPictures(ROTATE_CCW)"
      @rotate-right="rotateSelectedPictures(ROTATE_CW)"
    />

    <!-- ── Overlay (lightbox) context menu ─────────────────────
         A second, dedicated instance so grid-menu and overlay-menu state stay
         cleanly separated. It runs in overlay-mode (restricted action set +
         dark skin) and every action is scoped to the single overlay picture via
         overlay-specific handlers - never the grid selection. -->
    <ImageGridContextMenu
      overlay-mode
      :visible="overlayCtxVisible"
      :x="overlayCtxX"
      :y="overlayCtxY"
      :selected-image-ids="overlayCtxSelectedIds"
      :selected-character="String(selectionStore.selectedCharacter)"
      :scrapheap-pictures-id="String(SCRAPHEAP_PICTURES_ID)"
      :lock-reason="overlayCtxLockReason"
      :context-image="overlayCtxImage"
      @close="overlayCtxVisible = false"
      @save-picture="handleOverlaySave"
      @save-picture-as="handleOverlaySaveAs"
      @copy-picture="handleOverlayCopy"
      @share-picture="handleOverlayShare"
      @find-similar-faces="handleOverlayFindSimilarFaces"
      @reverse-image-search="handleOverlayReverseImageSearch"
      @segment="openOverlaySegmentDialog"
      @delete-selected="handleOverlayDelete"
      @remove-from-group="handleOverlayScrapheapRestore"
    />

    <!-- ── New person from the context menu (#645) ─────────────
         Grid-local instance of the person editor so the create-and-assign
         flow's state (the captured selection) stays owned by the grid.
         SideBar keeps its own instance for its own entry points. -->
    <CharacterEditor
      :open="createPersonOpen"
      :character="createPersonCharacter"
      :projects="createPersonProjects"
      @close="handleCreatePersonClose"
      @saved="handleCreatePersonSaved"
    />

    <!-- ── Revoke picture shares confirm dialog ───────────────── -->
    <v-dialog v-model="revokeSharesDialogOpen" max-width="380">
      <v-card>
        <v-card-title style="font-size: 1rem; padding: 16px 20px 8px">
          <v-icon size="16" style="margin-right: 6px; opacity: 0.7"
            >mdi-link-variant-off</v-icon
          >
          Remove all shares
        </v-card-title>
        <v-card-text
          style="padding: 0 20px 12px; font-size: 0.875rem; opacity: 0.85"
        >
          This will revoke all active share links for this image. Anyone with an
          existing link will lose access immediately.
        </v-card-text>
        <v-card-actions style="padding: 8px 16px 16px">
          <v-btn variant="text" @click="revokeSharesDialogOpen = false"
            >Cancel</v-btn
          >
          <v-spacer />
          <v-btn
            color="error"
            variant="tonal"
            @click="confirmRevokePictureShares"
          >
            Remove all shares
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- ── Segment (object detection) dialog ─────────────────── -->
    <v-dialog v-model="segmentDialogOpen" max-width="420">
      <v-card>
        <v-card-title style="font-size: 1rem; padding: 16px 20px 8px">
          <v-icon size="16" style="margin-right: 6px; opacity: 0.7"
            >mdi-shape-outline</v-icon
          >
          Detect objects
        </v-card-title>
        <v-card-text style="padding: 0 20px 4px">
          <div style="font-size: 0.875rem; opacity: 0.85; margin-bottom: 10px">
            Leave the label empty for dense object detection, or type a phrase
            to detect only that (e.g. "dog").
          </div>
          <v-text-field
            v-model="segmentPrompt"
            label="Label (optional)"
            density="comfortable"
            hide-details
            autofocus
            @keydown.enter.stop.prevent="confirmSegment"
          />
        </v-card-text>
        <v-card-actions style="padding: 8px 16px 16px">
          <v-btn variant="text" @click="segmentDialogOpen = false"
            >Cancel</v-btn
          >
          <v-spacer />
          <v-btn color="primary" variant="tonal" @click="confirmSegment">
            Detect
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- ── Share picture dialog ──────────────────────────────── -->
    <ShareDialog
      v-model="sharePicDialogOpen"
      resource-type="picture"
      :resource-id="contextMenuImage?.id"
      :resource-format="contextMenuImage?.format"
      :embed-watermark="userPrefsStore.embedWatermark"
      :public-url="userPrefsStore.publicUrl"
      @update:embed-watermark="userPrefsStore.embedWatermark = $event"
      @created="onSharePicCreated"
    />
    <EmptyScrapHeap
      v-if="showScrapheapBar"
      :visible="showScrapheapBar"
      :disabled="scrapheapEmptyDisabled"
      :restoreDisabled="scrapheapRestoreDisabled"
      :retention-label="scrapheapRetentionLabel"
      @empty-scrapheap="confirmEmptyScrapheap"
      @restore-scrapheap="confirmRestoreScrapheap"
      @open-retention-settings="emit('open-settings', 'scrapheap')"
    />
    <SnapshotsWithDeletedDialog
      v-model="snapshotsWithDeletedOpen"
      :snapshots="snapshotsWithDeleted"
      :dont-show-again="userPrefsStore.hidePurgeSnapshotWarning"
      @update:dont-show-again="
        userPrefsStore.setHidePurgeSnapshotWarning($event)
      "
    />
    <DeleteForeverDialog
      v-model:open="deleteForeverOpen"
      :total-count="deleteForeverTotalCount"
      :protected-count="deleteForeverProtectedCount"
      :unprotected-count="deleteForeverUnprotectedCount"
      :protected-paths="deleteForeverProtectedPaths"
      :locked-count="deleteForeverLockedCount"
      :busy="deleteForeverBusy"
      @confirm="confirmDeleteForever"
      @cancel="cancelDeleteForever"
    />
    <!-- The one consent for collapsing stacks to their covers. Deliberately NOT
         a sibling of DeleteForeverDialog's ceremony: no confirm token and no
         type-to-confirm, because this is the same recoverable soft delete the
         grid's Delete performs. -->
    <KeepCoverOnlyDialog
      :open="keepCoverOnlyOpen"
      :preview="keepCoverOnlyPreview"
      :loading="keepCoverOnlyLoading"
      :preview-failed="keepCoverOnlyPreviewFailed"
      :busy="keepCoverOnlyBusy"
      @close="closeKeepCoverOnly"
      @confirm="runKeepCoverOnly"
    />
    <div
      v-if="isMultiCharacterView || isSetOverlapView"
      class="multi-select-toolbar"
    >
      <select
        class="multi-select-toolbar__mode"
        :value="
          isMultiCharacterView
            ? selectionStore.characterMultiMode
            : selectionStore.setMultiMode
        "
        @change="
          (e) =>
            isMultiCharacterView
              ? selectionStore.setCharacterMultiMode(e.target.value)
              : selectionStore.setSetMultiMode(e.target.value)
        "
      >
        <option value="union">Union</option>
        <option value="intersection">Overlap</option>
        <option value="difference">Difference</option>
        <option value="xor">Unique (XOR)</option>
      </select>
      <template
        v-if="
          !isMultiCharacterView && selectionStore.setMultiMode === 'difference'
        "
      >
        <span class="multi-select-toolbar__separator">|</span>
        <label class="multi-select-toolbar__base-label">Base:</label>
        <select
          class="multi-select-toolbar__base"
          :value="
            selectionStore.setDifferenceBaseId ?? normalizedSelectedSetIds[0]
          "
          @change="
            (e) => selectionStore.setSetDifferenceBaseId(Number(e.target.value))
          "
        >
          <option
            v-for="sid in normalizedSelectedSetIds"
            :key="sid"
            :value="sid"
          >
            {{ selectionStore.selectedSetNames[sid] || `Set ${sid}` }}
          </option>
        </select>
      </template>
      <span class="multi-select-toolbar__label">
        {{
          isMultiCharacterView
            ? `${normalizedSelectedCharacterIds.length} people selected`
            : `${normalizedSelectedSetIds.length} sets selected`
        }}
      </span>
      <span class="multi-select-toolbar__spacer"></span>
      <button
        class="multi-select-toolbar__clear"
        title="Clear selection"
        @click="emit('clear-multi-selection')"
      >
        <v-icon size="16">mdi-selection-off</v-icon>
        Deselect All
      </button>
    </div>
    <ProgressOverlay
      :visible="exportProgress.visible"
      :status="exportProgress.status"
      :message="exportProgress.message"
      :percent="exportProgressPercent"
      :count="exportProgress.processed"
      :total="exportProgress.total"
      :abort-label="
        !['completed', 'failed', 'cancelled'].includes(exportProgress.status)
          ? 'Abort'
          : null
      "
      anchor="top"
      @abort="abortExport"
    />
    <RemixDialog
      :open="remixDialogOpen"
      :image="remixImage"
      :selected-image-ids="selectedImageIds"
      :client-id="comfyuiClientId || ''"
      :stack-outputs="genStackPrefs.stackI2IOutputs"
      @close="remixDialogOpen = false"
      @run="handleComfyuiRun"
      @use-batch="handleContextMenuOpenComfyuiPanel"
    />
    <ComfyUiRunner
      ref="comfyuiRunner"
      :wsPluginProgress="wsStore.wsPluginProgress"
      :overlayOpen="overlayOpen"
      :overlayImageId="overlayImageId"
      :allGridImages="allGridImages"
      :lastFetchedGridImages="lastFetchedGridImages"
      :getPictureStackId="getPictureStackId"
      :selectNewestStackMember="selectNewestStackMember"
      @refresh-grid="onComfyuiRefreshGrid"
      @refresh-sidebar="emit('refresh-sidebar')"
      @update:overlayImageId="
        (id) => {
          overlayImageId.value = id;
        }
      "
    />

    <ProgressOverlay
      :visible="pluginProgress.visible"
      :status="pluginProgress.status"
      :message="pluginProgress.message"
      :percent="pluginProgressPercent"
      :count="pluginProgress.current"
      :total="pluginProgress.total"
      anchor="bottom"
    />

    <ProgressOverlay
      :visible="smartScoreLoadingVisible"
      :status="smartScoreProgress.status"
      :message="smartScoreProgressMessage"
      :percent="smartScoreProgressPercent"
      :indeterminate="false"
      anchor="top"
    />

    <div
      class="grid-scroll-wrapper"
      ref="scrollWrapper"
      @scroll="onGridScroll"
      @dragenter.prevent="handleGridDragEnter"
      @dragover.prevent="handleGridDragOver"
      @dragleave.prevent="handleGridDragLeave"
      @drop.capture.prevent="handleGridDrop"
      :style="scrollWrapperStyle"
    >
      <div
        v-if="
          wsStore.pendingExternalImportCount > 0 ||
          wsStore.sortChangedExternalCount > 0
        "
        class="pending-imports-pill-anchor"
      >
        <button
          v-if="wsStore.pendingExternalImportCount > 0"
          class="pending-imports-pill"
          data-testid="pending-imports-pill"
          @click="emit('load-pending-imports')"
        >
          <v-icon :size="16" aria-hidden="true">mdi-image-plus-outline</v-icon>
          {{ wsStore.pendingExternalImportCount }}
          {{
            wsStore.pendingExternalImportCount === 1
              ? "new picture"
              : "new pictures"
          }}
          - Load
        </button>
        <button
          v-if="wsStore.sortChangedExternalCount > 0"
          class="pending-imports-pill"
          data-testid="sort-changed-pill"
          @click="emit('load-sort-changed')"
        >
          <v-icon :size="16" aria-hidden="true">mdi-refresh</v-icon>
          View changed externally - Refresh
        </button>
      </div>
      <div v-if="dragOverlayVisible" class="drag-overlay">
        <div class="drag-overlay-message">{{ dragOverlayMessage }}</div>
      </div>
      <div v-if="showFolderScanningState" class="empty-state">
        <div class="empty-state-card" role="status" aria-live="polite">
          <div class="empty-state-illustration" aria-hidden="true">
            <img
              src="/Empty.png"
              alt=""
              :style="emptyStateImageStyle"
            />
          </div>
          <div class="empty-state-title">PixlStash is scanning your folder</div>
          <div class="empty-state-subtitle">
            Preparing pictures and thumbnails…
          </div>
        </div>
      </div>
      <!-- All three routes reuse wiring App.vue already had: `local-import`
           reaches `SideBar.startLocalImport`, `open-settings` reaches
           `SideBar.openSettingsDialog`, and `choose-folder` is the one new
           signal, for the reference-folder editor the sidebar owns. -->
      <LibraryEmptyState
        v-if="showLibraryEmptyState"
        @choose-folder="emit('choose-folder')"
        @connect-comfyui="emit('open-settings', 'workflows')"
        @add-files="importChosenFiles"
      />
      <div v-else-if="showEmptyState" class="empty-state">
        <div class="empty-state-card">
          <div class="empty-state-illustration" aria-hidden="true">
            <img
              :src="emptyStateImage"
              alt=""
              :style="emptyStateImageStyle"
            />
          </div>
          <div class="empty-state-title">
            {{ emptyStateTitle }}
          </div>
          <div class="empty-state-subtitle">
            {{ emptyStateSubtitle }}
          </div>
          <v-btn
            v-if="canShowAllPicturesButton"
            class="empty-state-action app-btn-base"
            color="primary"
            variant="elevated"
            @click.stop="handleEmptyStateReset"
          >
            Show All Pictures
          </v-btn>
        </div>
      </div>
      <div
        :class="[
          'image-grid',
          {
            'compact-mode': gridStore.compactMode,
            'touch-select-mode': touchSelectMode,
            'image-grid--justified': isJustifiedMode,
          },
        ]"
        :style="gridContainerStyle"
        ref="gridContainer"
        data-testid="image-grid"
        role="grid"
        :aria-label="`${activeCategoryLabel || 'Pictures'} grid`"
        aria-multiselectable="true"
        :aria-busy="imagesLoading ? 'true' : 'false'"
        @click="handleGridBackgroundClick"
      >
        <!-- Top spacer for virtual scroll alignment (width 100% makes it a
             full flex line in justified mode; harmless in grid mode) -->
        <div
          v-if="topSpacerHeight > 0"
          :style="{
            gridColumn: '1 / -1',
            width: '100%',
            height: `${topSpacerHeight}px`,
          }"
        ></div>
        <div
          v-for="(img, idx) in gridImagesToRender"
          :key="img.id ? `img-${img.id}-${img.idx}` : `placeholder-${img.idx}`"
          :style="[getStackCardStyle(img), getJustifiedCardStyle(img, idx)]"
          :class="[
            'image-card',
            {
              'image-card-stack-expanded': isStackExpandedForImage(img),
              'image-card-stack-reorder-target': isStackReorderTarget(img),
              'image-card-stack-reorder-left': isStackReorderTargetSide(
                img,
                'left',
              ),
              'image-card-stack-reorder-right': isStackReorderTargetSide(
                img,
                'right',
              ),
              'stack-hover-active':
                hoveredStackId !== null &&
                getPictureStackId(img) === hoveredStackId,
              'image-card-cursor': img.idx === cursorIdx,
              'image-card--ghost': isImageGhosted(img),
            },
          ]"
          :ref="(element) => setImageCardRef(img.idx, element)"
          :role="img.id && !isImageGhosted(img) ? 'row' : 'presentation'"
          :tabindex="imageCardTabIndex(img)"
          :aria-label="img.id ? imageCardAriaLabel(img) : undefined"
          :aria-selected="
            img.id && !isImageGhosted(img)
              ? selectedImageIds.includes(img.id)
                ? 'true'
                : 'false'
              : undefined
          "
          :aria-hidden="!img.id || isImageGhosted(img) ? 'true' : undefined"
          :inert="isImageGhosted(img) || null"
          @click="handleImageCardClick(img, img.idx, $event)"
          @focus="handleImageCardFocus(img)"
          @mouseenter="handleImageMouseEnter(img)"
          @mouseleave="handleImageMouseLeave(img)"
          @contextmenu.prevent="handleImageContextMenu(img, $event)"
          @touchstart="handleTouchStart(img, img.idx, $event)"
          @touchmove.passive="handleTouchMove"
          @touchend.passive="handleTouchEnd"
        >
          <div
            :class="[
              'thumbnail-card',
              { 'thumbnail-card-new': isImageRecentlyAdded(img.id) },
            ]"
            :role="img.id && !isImageGhosted(img) ? 'gridcell' : 'presentation'"
            @click.stop="handleThumbnailClick(img, img.idx, $event)"
          >
            <div
              :class="[
                'thumbnail-container',
                {
                  'thumbnail-container-drag-source': isDragSourceImage(img),
                  'thumbnail-container--justified': isJustifiedMode,
                  'thumbnail-container--cropped': isSquareCropActive(img),
                },
              ]"
              :style="getJustifiedThumbStyle(img, idx)"
              :ref="(el) => setThumbnailContainerRef(img.id, el)"
              draggable="true"
              @dragstart.capture="handleContainerDragStart(img, $event)"
              @dragend.capture="handleDragEnd"
              @dragover="handleStackReorderDragOver(img, $event)"
              @drop="handleStackReorderDrop(img, $event)"
              @dragleave="handleStackReorderDragLeave(img, $event)"
            >
              <!-- Top-left permanent badges (top→bottom): problem, reference folder, lock, share -->
              <div
                v-if="
                  isThumbnailReady(img.id) &&
                  img.thumbnail &&
                  ((gridStore.showProblemIcon && hasPenalisedTags(img)) ||
                    img.reference_folder_id ||
                    lockedSetsStore.isLocked(img.id) ||
                    (!isReadOnly && sharedPictureIds.has(img.id)))
                "
                class="thumbnail-top-left-badges"
              >
                <div
                  v-if="gridStore.showProblemIcon && hasPenalisedTags(img)"
                  class="penalised-tag-indicator thumbnail-badge"
                  :title="penalisedTagsTitle(img)"
                >
                  <v-icon
                    :size="badgeIconSizes.penalised"
                    :color="
                      penalisedTagColor(img, userPrefsStore.penalisedTagWeights)
                    "
                    >{{
                      penalisedTagIcon(
                        img,
                        userPrefsStore.penalisedTagWeights,
                        userPrefsStore.themeMode !== "light",
                      )
                    }}</v-icon
                  >
                </div>
                <button
                  v-if="img.reference_folder_id"
                  type="button"
                  class="thumbnail-reference-badge thumbnail-badge"
                  :title="img.file_path || 'Reference picture'"
                  :aria-label="`Open reference location for ${imageCardAriaLabel(img)}`"
                  @click.stop="openReferenceLocation(img.id)"
                >
                  <v-icon :size="badgeIconSizes.penalised">mdi-folder</v-icon>
                </button>
                <div
                  v-if="lockedSetsStore.isLocked(img.id)"
                  class="thumbnail-lock-badge thumbnail-badge"
                  :title="lockedSetsStore.lockReason(img.id)"
                >
                  <v-icon :size="badgeIconSizes.penalised"
                    >mdi-lock-outline</v-icon
                  >
                </div>
                <div
                  v-if="!isReadOnly && sharedPictureIds.has(img.id)"
                  class="thumbnail-share-badge thumbnail-badge"
                  title="Has active share link"
                >
                  <v-icon :size="badgeIconSizes.penalised"
                    >mdi-link-variant</v-icon
                  >
                </div>
              </div>
              <!-- Scrapheap auto-purge state (permanent, bottom-left).
                   Either a countdown to the server's `purge_at` or, for a
                   protected reference original, the "won't auto-delete" badge.
                   Icon + text, never colour alone. -->
              <div
                v-if="getScrapheapPurgeBadge(img)"
                class="thumbnail-purge-badge"
                :class="`thumbnail-purge-badge--${getScrapheapPurgeBadge(img).kind}`"
                :title="getScrapheapPurgeBadge(img).title"
              >
                <v-icon size="12" class="thumbnail-purge-badge__icon">{{
                  getScrapheapPurgeBadge(img).icon
                }}</v-icon>
                <span class="thumbnail-purge-badge__text">{{
                  getScrapheapPurgeBadge(img).label
                }}</span>
              </div>
              <!-- Resolution overlay (always rendered, visible on hover) -->
              <div
                v-if="
                  img.width &&
                  img.height &&
                  isThumbnailReady(img.id) &&
                  img.thumbnail
                "
                :class="[
                  'resolution-hover-overlay',
                  'thumbnail-badge',
                  'thumbnail-badge--bottom-right',
                ]"
              >
                {{ img.width }}×{{ img.height }}
              </div>
              <template
                v-if="
                  getThumbnailSrc(img) &&
                  isVideo(img) &&
                  getVideoThumbnailSrc(img)
                "
              >
                <video
                  class="thumbnail-img"
                  aria-hidden="true"
                  :src="getVideoThumbnailSrc(img)"
                  :poster="getThumbnailSrc(img)"
                  :ref="
                    (el) => {
                      setVideoRef(img.id, el);
                      setThumbnailRef(img.id, el);
                    }
                  "
                  preload="none"
                  draggable="false"
                  @pointerdown="prepareThumbnailNativeDrag(img, $event)"
                  @pointerup="handleThumbnailPointerRelease($event)"
                  @pointercancel="handleThumbnailPointerRelease($event)"
                  @loadeddata="onThumbnailLoad(img.id, $event)"
                  muted
                  loop
                  playsinline
                  @mouseenter="playVideo(img.id)"
                  @mouseleave="pauseVideo(img.id)"
                ></video>
                <img
                  class="thumbnail-drag-preview"
                  :src="getThumbnailSrc(img)"
                  :ref="(el) => setDragPreviewRef(img.id, el)"
                  alt=""
                />
              </template>
              <template v-else-if="getThumbnailSrc(img)">
                <img
                  v-show="!failedThumbnailIds.has(img.id)"
                  :src="getThumbnailSrc(img)"
                  alt=""
                  class="thumbnail-img"
                  :style="getSquareCropImgStyle(img)"
                  :ref="(el) => setThumbnailRef(img.id, el)"
                  loading="eager"
                  fetchpriority="high"
                  decoding="async"
                  draggable="true"
                  @pointerdown="prepareThumbnailNativeDrag(img, $event)"
                  @pointerup="handleThumbnailPointerRelease($event)"
                  @pointercancel="handleThumbnailPointerRelease($event)"
                  @dragstart="handleThumbnailNativeDragStart(img, $event)"
                  @dragend="handleDragEnd"
                  @error="handleImageError(img, $event)"
                  @load="onThumbnailLoad(img.id, $event)"
                />
                <div
                  v-if="failedThumbnailIds.has(img.id)"
                  class="thumbnail-placeholder"
                >
                  <v-icon class="thumbnail-broken-icon"
                    >mdi-image-broken-variant</v-icon
                  >
                </div>
                <!-- In-flight rotate. Raised the moment the gesture is sent and
                     dropped when the tile actually turns, which is a beat later
                     than the click: the new bitmap is decoded first so the
                     shape and the picture change in one frame. Decorative -
                     the operation receipt is what announces the result. -->
                <div
                  v-if="rotatingIconFor(img)"
                  class="thumbnail-rotating-overlay"
                  data-testid="thumbnail-rotating-overlay"
                  aria-hidden="true"
                >
                  <v-icon class="thumbnail-rotating-icon">{{
                    rotatingIconFor(img)
                  }}</v-icon>
                </div>
                <img
                  v-if="isVideo(img)"
                  class="thumbnail-drag-preview"
                  :src="getThumbnailSrc(img)"
                  :ref="(el) => setDragPreviewRef(img.id, el)"
                  alt=""
                />
                <!-- Face bounding box overlays: must be rendered after the image for correct stacking -->
                <template v-if="isThumbnailReady(img.id) && img.thumbnail">
                  <button
                    v-for="overlay in getFaceBboxOverlays(img)"
                    :key="
                      overlay.faceId +
                      '-' +
                      img.id +
                      '-' +
                      (img.thumbnail ? 1 : 0)
                    "
                    type="button"
                    class="face-bbox-overlay face-bbox-overlay--interactive"
                    :style="overlay.style"
                    :aria-label="`Select face ${overlay.face.character_name || overlay.faceIdx + 1}`"
                    :aria-pressed="
                      isFaceSelected(img.id, overlay.faceIdx) ? 'true' : 'false'
                    "
                    draggable="true"
                    @pointerdown.stop
                    @mousedown.stop
                    @contextmenu.prevent.stop="
                      handleFaceBboxContextMenu(img, overlay, $event)
                    "
                    @click.stop="
                      toggleFaceSelection(
                        img.id,
                        overlay.faceIdx,
                        overlay.faceId,
                      )
                    "
                    @dragstart="
                      (e) => {
                        e.stopPropagation();
                        onFaceBboxDragStart(
                          e,
                          img,
                          overlay.faceIdx,
                          overlay.faceId,
                        );
                      }
                    "
                  >
                    <div
                      :style="{ color: overlay.color }"
                      class="face-bbox-label"
                    >
                      {{ overlay.face.character_name }}
                    </div>
                  </button>
                </template>
                <!-- Object detection (segmentation) overlays -->
                <template v-if="isThumbnailReady(img.id) && img.thumbnail">
                  <div
                    v-for="overlay in getDetectionBboxOverlays(img)"
                    :key="'det-' + overlay.detId + '-' + img.id"
                    class="face-bbox-overlay"
                    :style="overlay.style"
                  >
                    <div
                      :style="{ color: overlay.color }"
                      class="face-bbox-label"
                    >
                      {{ overlay.det.label }}
                    </div>
                  </div>
                </template>
                <div
                  v-if="
                    isThumbnailReady(img.id) &&
                    img.thumbnail &&
                    img.format &&
                    img.format !== 'unknown'
                  "
                  :class="[
                    'thumbnail-bottom-left-badges',
                    {
                      // The scrapheap purge badge is permanent and owns the
                      // bottom-left corner; the hover-only format badge stacks
                      // above it instead of landing on top of it.
                      'thumbnail-bottom-left-badges--raised':
                        !!getScrapheapPurgeBadge(img),
                    },
                  ]"
                >
                  <!-- Format badge: hover-only -->
                  <div class="thumbnail-id-overlay thumbnail-badge">
                    {{ img.format.toUpperCase() }}
                  </div>
                </div>
              </template>
              <template v-else>
                <div
                  class="thumbnail-placeholder thumbnail-placeholder--loading"
                  aria-hidden="true"
                ></div>
              </template>
              <!-- Stack band overlay (top+bottom color stripe for compact mode) -->
              <div
                v-if="getStackBandStyle(img)"
                class="stack-band-overlay"
                :style="getStackBandStyle(img)"
              ></div>
              <!-- The stack count and the deck edges are permanent, not
                   hover-only: how many pictures a tile stands for is a fact
                   about the tile, and hiding it until hover is what made stacks
                   invisible while browsing. -->
              <StackEdgeTicks
                v-if="shouldShowStackBadge(img) && stackDeckEdgesFit"
                :count="getStackBadgeCount(img)"
              />
              <!-- Once a stack is expanded its members look like any other
                   picture, so the one that the collapsed tile stands for has to
                   say so. Without this, expanding a stack loses the answer to
                   "which of these is the keeper". -->
              <span
                v-if="isExpandedStackCover(img)"
                class="stack-cover-flag"
                title="This picture is the stack's cover"
                >Cover</span
              >
              <!-- Top-right badge column - the shared home for corner
                   indicators (stack count in the corner, hover-only stars
                   below it, right-aligned, 2px gap). The stack count is
                   PERMANENT - how many pictures a tile stands for is a fact
                   about the tile, and hiding it until hover is what made
                   stacks invisible while browsing. The hover behaviour
                   therefore lives on the container's other children, not the
                   container.

                   The permanent badge leads the column deliberately: below the
                   stars its rest position was set by a strip that is
                   `opacity: 0` but still in flow, so it hung a row off the
                   corner and moved whenever the star size or `showStars`
                   changed. Leading, it never moves; only what appears beneath
                   it does. -->
              <div
                v-if="isThumbnailReady(img.id) && img.thumbnail"
                class="thumbnail-top-right-badges"
              >
                <StackBadge
                  v-if="shouldShowStackBadge(img)"
                  :count="getStackBadgeCount(img)"
                  :tint="getStackBadgeTint(img)"
                  @activate="toggleStackExpand(img)"
                  @mouseenter.stop="prefetchStackMembers(img)"
                />
                <StarRatingOverlay
                  v-if="gridStore.showStars"
                  :score="
                    isReadOnly
                      ? (guestScoreMap.get(img.id) ?? img.score ?? 0)
                      : img.score || 0
                  "
                  :icon-size="badgeIconSizes.star"
                  :compact="true"
                  @set-score="setScore(img, $event)"
                />
              </div>
            </div>
          </div>
          <div v-if="isImageSelected(img.id)" class="selection-overlay"></div>
          <!-- Info row absolutely positioned below thumbnail -->
          <div v-if="!gridStore.compactMode" class="thumbnail-info-row">
            <div
              v-for="info in getThumbnailInfoItems(img)"
              :key="`${info.key}-${img.id}`"
              class="thumbnail-info"
              :ref="
                (el) => setThumbnailInfoRef(img.id, info.key, info.text, el)
              "
              :title="getThumbnailInfoTitle(img.id, info.key)"
              @mouseenter="handleThumbnailInfoMouseEnter(img.id, info.key)"
            >
              {{ getThumbnailInfoDisplayText(img.id, info.key, info.text) }}
            </div>
          </div>
        </div>
        <!-- Bottom spacer -->
        <div
          v-if="bottomSpacerHeight > 0"
          :style="{
            gridColumn: '1 / -1',
            width: '100%',
            height: `${bottomSpacerHeight}px`,
          }"
        ></div>
      </div>
    </div>

    <!-- Guest scoring consent banner -->
    <v-snackbar
      v-if="isReadOnly"
      v-model="guestConsentBannerVisible"
      location="bottom"
      :timeout="-1"
      multi-line
      color="surface"
      elevation="4"
    >
      <span>
        To remember your ratings between visits, we need to store a small
        session cookie. Without it, your ratings will be lost when you close the
        browser.
      </span>
      <template #actions>
        <v-btn
          color="primary"
          variant="text"
          @click="handleGuestConsentAccepted"
        >
          Accept
        </v-btn>
        <v-btn
          color="default"
          variant="text"
          @click="handleGuestConsentRejected"
        >
          No thanks
        </v-btn>
      </template>
    </v-snackbar>

    <!-- Clear impossible tags result + Undo -->
    <v-snackbar
      v-model="impossibleSnackbarVisible"
      location="bottom"
      :timeout="8000"
      color="surface"
      elevation="4"
    >
      <span>{{ impossibleSnackbarText }}</span>
      <template #actions>
        <v-btn
          v-if="lastImpossibleRemoved.length"
          color="primary"
          variant="text"
          @click="handleUndoImpossibleTags"
        >
          Undo
        </v-btn>
        <v-btn
          color="default"
          variant="text"
          @click="impossibleSnackbarVisible = false"
        >
          Dismiss
        </v-btn>
      </template>
    </v-snackbar>

    <!-- The grid's ONE bottom-edge surface (merged-grid-action-pill.md). Before
         the merge the search bar and the selection pill were independent mounts
         that could both be up at once, and only the pill registered a bottom
         anchor - so notice cards landed on top of the search bar. -->
    <GridActionPill
      :search-active="searchResultsActive"
      :selection-active="showSelectionBar"
      @focus-escaped="restoreGridFocus"
    >
      <template #search>
        <SearchResultBar
          :images-loading="imagesLoading"
          :status-count="searchStatus.count"
          :status-label="searchStatus.label"
          :is-all-pictures-active="
            reverseImageSearchPictureIds.length ||
            faceLikenessSearchFaceId ||
            faceSearchCharacter
              ? true
              : selectionStore.isAllPicturesActive
          "
          :threshold="faceSearchCharacter ? faceSearchThreshold : null"
          :threshold-min="FACE_SEARCH_FETCH_FLOOR"
          :threshold-max="FACE_SEARCH_MAX_THRESHOLD"
          :min-refs="faceSearchMinRefs"
          :reference-count="faceSearchRefCount"
          :assign-target="faceSearchCharacter?.name ?? null"
          :assign-count="faceSearchAssignIds.length"
          :assign-from-selection="faceSearchAssignFromSelection"
          :assign-busy="faceSearchAssignBusy"
          :owns-escape="!showSelectionBar"
          @update:min-refs="handleFaceSearchMinRefs"
          @update:threshold="handleFaceSearchThreshold"
          @assign="handleAssignFaceSearchResults"
          @search-all="emit('search-all')"
          @clear="clearSearchQuery"
        />
      </template>

      <template #selection>
        <SelectionBar
          ref="selectionBarRef"
          :selected-count="selectedImageIds.length"
          :selected-expanded-count="selectedExpandedCount"
          :selected-face-count="selectedFaceIds.length"
          :selected-group-name="selectedGroupName"
          :selected-sort="sortStore.selectedSort"
          :owns-escape="true"
          :scrapheap-pictures-id="String(SCRAPHEAP_PICTURES_ID)"
          :selected-image-ids="selectedImageIds"
          :selected-media-support="selectedMediaSupport"
          :comfyui-client-id="comfyuiClientId"
          :comfyui-configured="filterStore.comfyuiConfigured"
          :show-remove-from-stack="showRemoveFromStack"
          :selected-multiple-stack-ids="selectedMultipleStackIds"
          :keep-cover-only-stack-count="keepCoverOnlyStackCount"
          :keep-cover-only-lock-reason="keepCoverOnlyLockReason"
          :grouping-lock-reason="partialStackGroupingReason"
          :rotate-block-reason="selectionRotateBlockReason"
          :available-plugins="availablePlugins"
          :tagger-plugins="taggerPlugins"
          :captioner-plugins="captionerPlugins"
          :all-grid-images="allGridImages"
          :selected-character="String(selectionStore.selectedCharacter)"
              :impossible-sources="filterStore.impossibleSources"
          :clearing-impossible="clearingImpossibleTags"
          @clear-impossible-tags="handleClearImpossibleTags"
          @clear-selection="clearSelection"
          @added-to-set="handleOverlayAddedToSet"
          @remove-from-group="removeFromGroup"
          @keep-cover-only="openKeepCoverOnly"
          @delete-selected="deleteSelected"
          @set-project="handleSetProjectForSelected"
          @add-to-character="handleAddToCharacter"
          @remove-from-character="handleRemoveFromCharacter"
          @create-stack="createStackFromSelection"
          @remove-from-stack="removeSelectedFromStack"
          @dissolve-stacks="dissolveSelectedStacks"
          @create-stacks-from-groups="createStacksFromSelectedGroups"
          @run-plugin="handlePluginRunRequest"
          @comfyui-run="handleComfyuiRun"
          @tags-applied="handleTagsApplied"
          @auto-tag="handleAutoTag"
          @generate-description="handleGenerateDescription"
          @reverse-image-search="handleReverseImageSearch"
          @segment="openSegmentDialog"
          @rotate-left="rotateSelectedPictures(ROTATE_CCW)"
          @rotate-right="rotateSelectedPictures(ROTATE_CW)"
          @selection-menu-open="toolbarSelectionMenuOpen = $event"
        />
      </template>
    </GridActionPill>
    <!-- The action receipt shares the selection pill's slot and lifts clear of
         it when both are up. Owner-only, like the toolbar control.

         `pill-hidden` while the lightbox is open, NOT `v-if`: the lightbox has
         its own narration of the same single receipt, so two pills would render
         it at two positions and the grid one would show through the backdrop.
         The component stays mounted because it carries the one app-wide
         `role="status"` region, which the lightbox deliberately does not
         duplicate (the lightbox does not `inert` the grid, so that region still
         speaks and a second one would double-speak). -->
    <ActionReceipt
      v-if="!isReadOnly"
      :lift-px="actionReceiptLift"
      :pill-hidden="overlayOpen"
    />
  </div>
</template>

<script setup>
import {
  computed,
  onMounted,
  reactive,
  ref,
  watch,
  nextTick,
  onUnmounted,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { useFilterStore } from "../../stores/useFilterStore";
import { useGridStore } from "../../stores/useGridStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import LibraryEmptyState from "./LibraryEmptyState.vue";
import { isSupportedImportFile } from "../../utils/media";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import { useGenStackPrefsStore } from "../../stores/useGenStackPrefsStore";
import { useScrapheapRetentionStore } from "../../stores/useScrapheapRetentionStore";
import {
  GHOST_PENDING,
  useOperationStore,
} from "../../stores/useOperationStore";
import { useNoticeStore, DEFAULT_TIMEOUTS } from "../../stores/useNoticeStore";
import { useBreadcrumb } from "../../composables/useBreadcrumb";
import {
  useAnchorHeight,
  useBottomAnchor,
} from "../../composables/useBottomAnchor";
import { FLOATING_BOTTOM_GAP_PX } from "../../utils/floatingBottom";
import { markEnd, markStart } from "../../utils/perfMarks";
import { useScopedNotice } from "../../composables/useScopedNotice";
import { buildPurgeBadge } from "../../utils/retention.js";
import { buildLockedDeleteMessage } from "../../utils/lockedDelete.js";
import {
  cutFaceSuggestions,
  referenceFaceCount,
} from "../../utils/faceSuggestionCut.js";
import {
  squareCropParams,
  squareCropImgStyle,
  squareCropBboxRect,
  coverBboxRect,
} from "../../utils/squareCrop.js";
import { errorMessage } from "../../utils/apiError.js";
import {
  isSupportedImageFile,
  isSupportedVideoFile,
  isVideo,
  getPictureId,
  buildMediaUrl,
  displayedAspectRatio,
} from "../../utils/media.js";
import {
  pictureGridLabel,
  pictureGridTabIndex,
} from "../../utils/gridAccessibility.js";
import ImageImporter from "../io/ImageImporter.vue";
import ImageOverlay from "./ImageOverlay.vue";
import EmptyScrapHeap from "../widgets/EmptyScrapHeap.vue";
import Toolbar from "../panels/Toolbar.vue";
import SelectionBar from "../panels/SelectionBar.vue";
import GridActionPill from "../panels/GridActionPill.vue";
import ActionReceipt from "../widgets/ActionReceipt.vue";
import ImageGridContextMenu from "../widgets/ImageGridContextMenu.vue";
import SearchResultBar from "../widgets/SearchResultBar.vue";
import StarRatingOverlay from "../widgets/StarRatingOverlay.vue";
import StackBadge from "../widgets/StackBadge.vue";
import StackEdgeTicks from "../widgets/StackEdgeTicks.vue";
import ComfyUiRunner from "../io/ComfyUiRunner.vue";
import RemixDialog from "../io/RemixDialog.vue";
import ProgressOverlay from "../widgets/ProgressOverlay.vue";
import ShareDialog from "../io/ShareDialog.vue";
import SnapshotsWithDeletedDialog from "../widgets/SnapshotsWithDeletedDialog.vue";
import DeleteForeverDialog from "../widgets/DeleteForeverDialog.vue";
import KeepCoverOnlyDialog from "../widgets/KeepCoverOnlyDialog.vue";
import {
  API_BASE_URL,
  appendShareToken,
  isReadOnly,
} from "../../utils/apiClient";
import {
  getPictureMetadata,
  getThumbnails,
  deletePictures,
  setPicturesProject,
  previewScrapheapDelete,
  purgeScrapheap,
  restoreScrapheap,
  openPictureLocation,
  detectPictures,
  listPicturePlugins,
  runPicturePlugin,
  resetPicturesTags,
  resetPicturesDescriptions,
  clearImpossibleTags,
  restoreImpossibleTags,
  startExport,
  startFolderExport,
  getExportStatus,
  downloadExport,
  listPicturesByIds,
  rotatePictures,
} from "../../api/pictures";
import { addPictureTag } from "../../api/tags";
import {
  getStack,
  keepCoverOnly,
  previewKeepCoverOnly,
  removeStackMembers,
} from "../../api/stacks";
import {
  keepCoverOnlyLockReason as buildKeepCoverOnlyLockReason,
  keepCoverOnlySkipNote,
  selectedKeepCoverOnlyStacks,
} from "../../utils/keepCoverOnly";
import {
  ROTATE_CCW,
  ROTATE_CW,
  ROTATE_OP_TYPE,
  rotateBlockReason as buildRotateBlockReason,
  rotateSkipNote,
} from "../../utils/rotate";
import {
  getCharacter,
  listCharacters,
  addCharacterFaces,
  addCharacterFaceAssignments,
  addCharacterFacesByFaceId,
  removeCharacterFaces,
  removeCharacterFacesByFaceId,
} from "../../api/characters";
import { listProjects } from "../../api/projects";
import {
  chooseCharacterAssignment,
  nextFreeCharacterName,
} from "../../utils/characterCreateFlow.js";
import CharacterEditor from "../editors/CharacterEditor.vue";
import {
  getPictureSet,
  addPictureToSet,
  removePictureFromSet,
} from "../../api/pictureSets";
import { getSharedPictureIds, revokeTokensByResource } from "../../api/users";
import { listTaggers } from "../../api/taggers";
import { runTextToImage } from "../../api/comfyui";
import {
  faceBoxColor,
  formatUserDate,
  getInfoFont,
  isRangeOverlap,
  normalizePluginProgressMessage,
  rangeCovers,
  sleep,
} from "../../utils/utils.js";
import {
  isLockedRefusal,
  lockedSets,
  lockedSetsSentence,
} from "../../utils/dedup.js";
import {
  dedupeTagList,
  hasPenalisedTags,
  penalisedTagsTitle,
  penalisedTagIcon,
  penalisedTagColor,
  getTagList,
} from "../../utils/tags.js";
import {
  getStackBadgeCount,
  getPictureStackId,
  getStackPositionValue,
  selectNewestStackMember,
  shouldShowStackBadge,
} from "../../utils/stack.js";
import { useVirtualScroll } from "../../composables/useVirtualScroll.js";
import {
  rowOfIndex,
  JUSTIFIED_ROW_GAP,
} from "../../composables/useJustifiedLayout.js";
import { useMultiSelect } from "../../composables/useMultiSelect.js";
import { useGridDragDrop } from "../../composables/useGridDragDrop.js";
import { useStackOrdering } from "../../composables/useStackOrdering.js";
import { useGridFetch } from "../../composables/useGridFetch.js";
import { useGridScoring } from "../../composables/useGridScoring.js";
import { useGridKeyboardNav } from "../../composables/useGridKeyboardNav.js";
import { debounce } from "../../utils/utils";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useSortStore } from "../../stores/useSortStore";
import { useProjectStore } from "../../stores/useProjectStore";
import { useWsStore } from "../../stores/useWsStore";
import { useSearchStore } from "../../stores/useSearchStore";
import {
  ALL_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
  UNASSIGNED_PICTURES_ID,
} from "../../stores/useViewStore";

// Store-direct (Phase 3): the picture-query filter facets and the grid's own
// display preferences are read from the stores, not mirrored in through props.
const filterStore = useFilterStore();
const selectionStore = useSelectionStore();
const sortStore = useSortStore();
const projectStore = useProjectStore();
const wsStore = useWsStore();
const searchStore = useSearchStore();
const gridStore = useGridStore();
const sidebarStore = useSidebarStore();
const userPrefsStore = useUserPrefsStore();

const emit = defineEmits([
  "update:overlay-open",
  "refresh-sidebar",
  "clear-search",
  "reset-to-all",
  "search-all",
  "update:stack-stats",
  "import-started",
  "import-ended",
  "clear-multi-selection",
  "update:visible-range-label",
  "update:match-count",
  "load-pending-imports",
  "load-sort-changed",
  "flag-sort-changed",
  "open-settings",
  "open-duplicates",
  "open-import",
  "local-import",
  "confirm-export-zip",
  "confirm-export-folder",
  // The empty library's folder route. The reference-folder editor is the
  // sidebar's, and App.vue already holds the ref that reaches it.
  "choose-folder",
  // The empty library was shown. The sidebar checks whether the library's own
  // folder holds pictures nothing has indexed and offers to bring them in.
  "library-empty",
  "library-loaded",
]);

// Props
const props = defineProps({
  // Defaulted, not threaded from App.vue. Without the default this is
  // `undefined` and every URL built from it reads "undefined/pictures/…",
  // which is every thumbnail in the grid.
  backendUrl: { type: String, default: () => API_BASE_URL },
  activeCategoryLabel: { type: String, default: "Category" },
});

// ============================================================
// CONSTANTS
// ============================================================
const LIKENESS_GROUPS_SORT_KEY = "LIKENESS_GROUPS";
// Per-column thumbnail width is now driven by `1fr` tracks that fill the grid;
// the 288px upper bound (useViewportLayout.js MAX_THUMBNAIL_SIZE) is enforced
// via the column-count clamp (updateMaxColumns), so no fixed max is applied to
// the track itself here.
const THUMBNAIL_INFO_ROW_HEIGHT = 24;

const normalizedSelectedSetIds = computed(() => {
  const idsFromProp = Array.isArray(selectionStore.selectedSetIds)
    ? selectionStore.selectedSetIds
    : [];
  const normalized = idsFromProp
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  if (normalized.length > 0) {
    return Array.from(new Set(normalized));
  }
  const single = Number(selectionStore.selectedSet);
  if (Number.isFinite(single) && single > 0) {
    return [single];
  }
  return [];
});

const hasSetSelection = computed(
  () => normalizedSelectedSetIds.value.length > 0,
);
const isSetOverlapView = computed(
  () => normalizedSelectedSetIds.value.length > 1,
);
const primarySelectedSetId = computed(() =>
  normalizedSelectedSetIds.value.length
    ? normalizedSelectedSetIds.value[0]
    : null,
);

const normalizedSelectedCharacterIds = computed(() => {
  const ids = Array.isArray(selectionStore.selectedCharacterIds)
    ? selectionStore.selectedCharacterIds
    : [];
  return ids
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0)
    .sort((a, b) => a - b);
});
const isMultiCharacterView = computed(
  () => normalizedSelectedCharacterIds.value.length > 1,
);

// True when project membership is part of the grid query, i.e. when the view is
// scoped to a project (or to the "unassigned project" pseudo-view). This mirrors
// useGridFetch._appendSelectionParams, which appends `project_id` only in this
// mode; in the global mode nothing about the query depends on project
// membership. Used to decide whether a project assignment can change what the
// grid shows and therefore warrants a refetch.
const isProjectScopedView = computed(
  () => projectStore.projectViewMode === "project",
);

// ============================================================
// THUMBNAIL SYSTEM STATE
// ============================================================
// Store refs for each thumbnail image (non-reactive to avoid render feedback loops)
const thumbnailRefs = {};
const thumbnailContainerRefs = {};
const dragPreviewRefs = {};
const thumbnailInfoRefs = {};
const thumbnailInfoTitleMap = reactive({});
const thumbnailInfoDisplayMap = reactive({});
const thumbnailInfoFullMap = reactive({});
const textMeasureCanvas =
  typeof document !== "undefined" ? document.createElement("canvas") : null;
const textMeasureContext = textMeasureCanvas
  ? textMeasureCanvas.getContext("2d")
  : null;
const thumbnailLoadedMap = reactive({});
const thumbnailReadyMap = reactive({});
const thumbnailAssignedAtMap = reactive({});

const THUMBNAIL_RETRY_DELAY_MS = 10000;
const THUMBNAIL_RETRY_LIMIT = 1;
const thumbnailRetryTimers = new Map();
const thumbnailRetryCounts = reactive({});
const PREFETCHED_FULL_IMAGE_LIMIT = 12;
const fullImagePrefetchControllers = new Map();
const prefetchedFullImageIds = new Set();
const prefetchedFullImageOrder = [];

// ============================================================
// DOM ELEMENT REFS
// ============================================================
const gridContainer = ref(null);
const scrollWrapper = ref(null);
const selectionBarRef = ref(null);
const toolbarSelectionMenuOpen = ref(false);
const contextMenuVisible = ref(false);
const contextMenuX = ref(0);
const contextMenuY = ref(0);
const contextMenuImage = ref(null);
const contextMenuClickedFace = ref(null);
// ── Overlay (lightbox) context menu - state kept separate from the grid menu.
// The image object arrives in the request payload from ImageOverlay (it owns
// the currently-displayed picture + its loaded faces), so this never depends on
// the grid selection.
const overlayCtxVisible = ref(false);
const overlayCtxX = ref(0);
const overlayCtxY = ref(0);
const overlayCtxImage = ref(null);
const imageOverlayRef = ref(null);
// The overlay menu acts on exactly one picture: the one on screen.
const overlayCtxSelectedIds = computed(() =>
  overlayCtxImage.value?.id != null ? [overlayCtxImage.value.id] : [],
);
const overlayCtxLockReason = computed(() =>
  overlayCtxImage.value?.id != null
    ? lockedSetsStore.lockReason(overlayCtxImage.value.id)
    : null,
);
const reverseImageSearchPictureIds = ref([]);
const faceLikenessSearchFaceId = ref(null);
// ── "Suggest more pictures of <person>" (#636) ────────────────────────────────
// The person whose reference faces are the active query, or null. Distinct from
// `selectionStore.selectedCharacter`: this search deliberately runs across the whole
// library, so it must not be mistaken for a character-scoped view.
const faceSearchCharacter = ref(null); // { id, name }
// The cut applied to the ranked list. Starts at the same value the backend's
// SourceFaceLikenessTask already treats as "same person, safe to inherit a
// character automatically" - a second, UI-local number would drift from it.
const FACE_SEARCH_DEFAULT_THRESHOLD = 0.7;
// The fetch floor. Pictures below it are never fetched, so the slider cannot be
// dragged under it without a refetch; that is why it is also the slider's min.
const FACE_SEARCH_FETCH_FLOOR = 0.5;
const FACE_SEARCH_MAX_THRESHOLD = 0.95;
const faceSearchThreshold = ref(FACE_SEARCH_DEFAULT_THRESHOLD);
// How many of the person's reference faces must clear that cut. Defaults to 1,
// which is the behaviour the backend's `combine=max` gives on its own, so the
// knob starts where the search has always been and only ever tightens.
const faceSearchMinRefs = ref(1);
// { characterId, matches: [{picture_id, likeness, face_id, reference_likeness}],
// rowsById }: the whole ranked list plus its picture rows, so re-cutting it on
// either knob is free.
const faceSearchRanked = ref(null);
// The view the search was armed from. A view change drops the search (below),
// and this is what keeps the arming click itself from counting as one.
const faceSearchArmedView = ref(null);
const faceSearchAssignBusy = ref(false);
const sharedPictureIds = ref(new Set());
const revokeSharesDialogOpen = ref(false);
const revokeSharesPending = ref(null); // { pictureId }
// Share picture dialog
const sharePicDialogOpen = ref(false);
// Segment (object detection) dialog
const segmentDialogOpen = ref(false);
const segmentPrompt = ref("");
// When set, confirmSegment targets these ids instead of the grid selection.
// Used by the overlay context menu to scope segmentation to the single picture
// on screen. Null → fall back to the grid selection (the normal grid path).
const segmentTargetIds = ref(null);

// ============================================================
// GRID DATA STATE
// ============================================================

// Badge size interpolated continuously across column count (1 = lg, 12+ = sm).
const badgeSizeT = computed(() =>
  Math.min(1, Math.max(0, ((gridStore.columns || 1) - 1) / 11)),
);

const badgeCssVars = computed(() => {
  const t = badgeSizeT.value;
  const fontSize = (0.8 - 0.3 * t).toFixed(3);
  const paddingV = Math.round(2 * (1 - t));
  const paddingH = Math.round(4 - 2 * t);
  return {
    "--badge-font-size": `${fontSize}em`,
    "--badge-padding": `${paddingV}px ${paddingH}px`,
  };
});

/**
 * Whether this tile is the cover of a stack the user has expanded.
 *
 * Only meaningful while the stack is expanded: collapsed, the cover IS the
 * tile, so flagging it would be noise.
 *
 * @param {Object} img
 * @returns {boolean}
 */
function isExpandedStackCover(img) {
  return isStackExpandedForImage(img) && getStackPositionValue(img) === 0;
}

const badgeIconSizes = computed(() => {
  const t = badgeSizeT.value;
  return {
    stack: Math.round(24 - 12 * t),
    penalised: Math.round(24 - 12 * t),
    star: Math.round(22 - 12 * t),
  };
});

const allGridImages = ref([]);

// ---- Clear impossible tags (bulk action) ----
const clearingImpossibleTags = ref(false);
const impossibleSnackbarVisible = ref(false);
const impossibleSnackbarText = ref("");
// Stash of the last clear's removed {picture_id, tag} pairs, for Undo.
const lastImpossibleRemoved = ref([]);

async function handleClearImpossibleTags() {
  const pictureIds = selectedImageIds.value
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  const filters = Array.isArray(filterStore.impossibleSources)
    ? filterStore.impossibleSources
    : [];
  if (!pictureIds.length || !filters.length || clearingImpossibleTags.value) {
    return;
  }
  clearingImpossibleTags.value = true;
  try {
    const body = await clearImpossibleTags(pictureIds, filters);
    const removed = Array.isArray(body?.removed) ? body.removed : [];
    const count = typeof body?.count === "number" ? body.count : removed.length;
    lastImpossibleRemoved.value = removed;
    impossibleSnackbarText.value =
      count > 0
        ? `Removed ${count} tag${count === 1 ? "" : "s"}`
        : "No impossible tags found";
    impossibleSnackbarVisible.value = true;
    debouncedFetchAllGridImages({ force: true });
  } catch (e) {
    console.error("[ImageGrid.vue] Failed to clear impossible tags:", e);
    lastImpossibleRemoved.value = [];
    impossibleSnackbarText.value = "Failed to clear impossible tags";
    impossibleSnackbarVisible.value = true;
  } finally {
    clearingImpossibleTags.value = false;
  }
}

async function handleUndoImpossibleTags() {
  const pairs = lastImpossibleRemoved.value;
  if (!Array.isArray(pairs) || !pairs.length) return;
  try {
    await restoreImpossibleTags(pairs);
    impossibleSnackbarVisible.value = false;
    lastImpossibleRemoved.value = [];
    debouncedFetchAllGridImages({ force: true });
  } catch (e) {
    console.error("[ImageGrid.vue] Failed to restore impossible tags:", e);
    impossibleSnackbarText.value = "Failed to undo";
    impossibleSnackbarVisible.value = true;
  }
}

// ---- Guest scoring state (READ-token users) ----
// null = not yet decided, 'accepted' = cookie consent given, 'rejected' = declined
const guestConsentState = ref(null);
const guestSessionId = ref(null);
// Map<picture_id (number), score (0-5)>
const guestScoreMap = ref(new Map());
const guestConsentBannerVisible = ref(false);
// Intent queued while the consent banner is shown
const pendingGuestScoreIntent = ref(null);
// ------------------------------------------------

const lastFetchedGridImages = ref([]);
// Track loaded batch ranges to avoid duplicate requests (used by thumbnail
// loading and stack composable)
const loadedRanges = ref([]);
let pendingRanges = [];

// ============================================================
// PLUGIN / EXPORT STATE
// ============================================================
const exportProgress = reactive({
  visible: false,
  status: "idle",
  processed: 0,
  total: 0,
  message: "",
  cancelRequested: false,
});

const pluginProgress = reactive({
  visible: false,
  status: "idle",
  current: 0,
  total: 0,
  percent: 0,
  message: "",
  runId: "",
});
let pluginProgressHideTimer = null;

const smartScoreProgress = reactive({
  visible: false,
  percent: 0,
  message: "Calculating smart scores",
  // Real status, not a hardcoded "running": ProgressOverlay derives both its
  // aria-busy and its completion announcement from this, so a sort that never
  // leaves "running" ends in silence for a screen-reader user (#758).
  status: "idle",
});
const SORT_PROGRESS_ESTIMATE_DEFAULT_MS = 2500;
const SORT_PROGRESS_ESTIMATE_SMART_SCORE_MS = 9000;
const SORT_PROGRESS_ESTIMATE_MIN_MS = 900;
const SORT_PROGRESS_ESTIMATE_MAX_MS = 45000;
const SORT_PROGRESS_EWMA_ALPHA = 0.25;
const SORT_PROGRESS_MAX_BEFORE_DONE = 97;
const SORT_PROGRESS_COMPLETION_HOLD_MS = 220;
const SORT_PROGRESS_WARM_RESTART_WINDOW_MS = 1200;
const sortEstimatedDurationMsByKey = reactive({});
let smartScoreProgressTimer = null;
let smartScoreProgressLoadId = 0;
let smartScoreProgressStartedAt = 0;
const smartScoreProgressSortKey = ref("");

const availablePlugins = ref([]);

async function fetchAvailablePlugins() {
  if (!props.backendUrl) {
    availablePlugins.value = [];
    return;
  }
  try {
    const body = await listPicturePlugins({ baseUrl: props.backendUrl });
    const plugins = Array.isArray(body?.plugins) ? body.plugins : [];
    availablePlugins.value = plugins.filter((plugin) => plugin && plugin.name);
  } catch (err) {
    console.warn("Failed to load image plugins:", err);
    availablePlugins.value = [];
  }
}

const allTaggerPlugins = ref([]);
const taggerPlugins = computed(() =>
  allTaggerPlugins.value.filter((p) => p.supports_tags),
);
const captionerPlugins = computed(() =>
  allTaggerPlugins.value.filter((p) => p.supports_descriptions),
);

async function fetchTaggerPlugins() {
  if (!props.backendUrl) return;
  try {
    const body = await listTaggers({ baseUrl: props.backendUrl });
    allTaggerPlugins.value = body?.plugins ?? [];
  } catch (err) {
    console.warn("Failed to load tagger plugins:", err);
    allTaggerPlugins.value = [];
  }
}

function handleTagsApplied(payload) {
  // A reset only marks pictures for the background tagger; the grid shows
  // nothing of that until the tagger's own tags_changed event lands, so the
  // reload that blanked the grid buys nothing here (#1162).
  if (payload?.action === "reset") return;
  debouncedFetchAllGridImages({ force: true });
}

async function handleAutoTag({ model } = {}) {
  const ids = selectedImageIds.value
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  if (!ids.length || !props.backendUrl) return;
  try {
    // One request marks the whole selection. No grid reload: the backend's
    // origin-stamped tags_changed event already refreshes a tag-filtered grid,
    // and reloading every card for a change that touches none of their
    // thumbnails is what blanked the grid (#1162).
    await resetPicturesTags(ids, model ? { model } : {});
  } catch (err) {
    console.error("Auto-tag failed:", err);
  }
}

async function handleGenerateDescription({ model } = {}) {
  const ids = selectedImageIds.value
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  if (!ids.length || !props.backendUrl) return;
  try {
    // One request marks the whole selection; each finished caption arrives
    // over descriptions_changed and refreshes its own card. No grid reload:
    // that is what blanked the grid (#1162).
    await resetPicturesDescriptions(ids, model ? { model } : {});
  } catch (err) {
    console.error("Generate description failed:", err);
  }
}

function handlePluginRunRequest(payload) {
  const pluginName = String(payload?.pluginName || "").trim();
  const pictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id) && id > 0)
    : [];
  const parameters =
    payload?.parameters && typeof payload.parameters === "object"
      ? payload.parameters
      : {};
  if (!pluginName || !pictureIds.length) return;
  // Stack the derived outputs with their originals unless the caller opted out.
  // Default true keeps the historical behaviour for any other run-plugin source.
  const stack = payload?.stack !== false;
  // Build per-image captions from stored descriptions in the grid.
  const idSet = new Set(pictureIds);
  const idToDesc = new Map();
  for (const img of allGridImages.value) {
    const id = Number(img?.id);
    if (idSet.has(id)) idToDesc.set(id, img.description || "");
  }
  const captions = pictureIds.map((id) => idToDesc.get(id) ?? "");
  runPluginWithParameters(pluginName, pictureIds, parameters, captions, stack);
}

async function runPluginWithParameters(
  pluginName,
  pictureIds,
  parameters,
  captions,
  stack = true,
) {
  if (!pluginName || !Array.isArray(pictureIds) || !pictureIds.length) return;
  try {
    const res = await runPicturePlugin(
      pluginName,
      {
        picture_ids: pictureIds,
        parameters: parameters || {},
        captions: Array.isArray(captions) ? captions : undefined,
        stack,
      },
    );
    const createdIds = Array.isArray(res?.created_picture_ids)
      ? res.created_picture_ids
      : [];
    // A deterministic filter re-run produces a byte-identical output, which the
    // importer correctly refuses as a duplicate. Nothing is created and nothing
    // moves on screen, so without this the run is indistinguishable from one
    // that silently did nothing.
    //
    // De-duplicated because the backend builds `duplicate_picture_ids` by
    // walking the output hashes (`image_plugins/service.py`), so two sources
    // that filter down to the same image report the same picture id twice -
    // and the sentence counts pictures in the library, not outputs produced.
    const duplicateIds = Array.isArray(res?.duplicate_picture_ids)
      ? res.duplicate_picture_ids
      : [];
    const duplicateCount = new Set(duplicateIds).size;
    if (duplicateCount) {
      const subject = duplicateCount === 1 ? "image is" : "images are";
      const label =
        availablePlugins.value.find((plugin) => plugin?.name === pluginName)
          ?.display_name || pluginName;
      noticeStore.info(
        `${label}: ${duplicateCount} ${subject} already in your library`,
        // Keyed per plugin, not globally: a second run of the SAME filter is a
        // repeat of this sentence and should coalesce, while a run of a
        // DIFFERENT one is its own report and must not overwrite this text and
        // then wear a count badge that contradicts it.
        { key: `plugin-run-duplicate:${pluginName}` },
      );
    }
    if (createdIds.length) {
      const newIds = createdIds
        .map((id) => getPictureId(id))
        .filter((id) => id != null);
      if (newIds.length) {
        triggerNewImageHighlight(newIds);
        if (overlayOpen.value) {
          overlayImageId.value = newIds[newIds.length - 1];
        }
      }
    }
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages();
    if (overlayOpen.value && pictureIds.length) {
      const refreshId =
        createdIds.length > 0
          ? createdIds[createdIds.length - 1]
          : pictureIds[0];
      await refreshGridImage(refreshId, { force: true });
    }
  } catch (err) {
    console.error("Failed to run plugin:", err);
    noticeStore.error(`Plugin run failed. ${errorDetail(err)}`, {
      key: "plugin-run",
    });
  }
}

const pluginProgressPercent = computed(() => {
  const percent = Number(pluginProgress.percent) || 0;
  return Math.min(100, Math.max(0, Math.round(percent)));
});

const smartScoreProgressPercent = computed(() => {
  const percent = Number(smartScoreProgress.percent) || 0;
  return Math.min(100, Math.max(0, Math.round(percent)));
});

const smartScoreProgressMessage = computed(
  () => smartScoreProgress.message || "Calculating smart scores",
);

function getSortProgressLabel(sortKey) {
  const key = String(sortKey || "").toUpperCase();
  if (!key) return "results";
  if (key.includes("SMART_SCORE")) return "smart score";
  if (key.includes("CHARACTER_LIKENESS")) return "character likeness";
  if (key === "TEXT_CONTENT") return "text content";
  if (key === "SCORE") return "score";
  if (key === LIKENESS_GROUPS_SORT_KEY) return "likeness groups";
  return key.replace(/_/g, " ").toLowerCase();
}

function getSortEstimateDefaultMs(sortKey) {
  const key = String(sortKey || "").toUpperCase();
  if (key.includes("SMART_SCORE")) return SORT_PROGRESS_ESTIMATE_SMART_SCORE_MS;
  return SORT_PROGRESS_ESTIMATE_DEFAULT_MS;
}

const exportProgressPercent = computed(() => {
  if (!exportProgress.total) return 0;
  const percent = (exportProgress.processed / exportProgress.total) * 100;
  return Math.min(100, Math.max(0, Math.round(percent)));
});

// ============================================================
// RECENTLY ADDED / WS STATE
// ============================================================
const recentlyAddedIds = ref({});
const recentlyAddedTimers = new Map();
const previousImageIds = new Set();
const hasLoadedOnce = ref(false);
const highlightNextFetch = ref(false);
const lastWsUpdateKey = ref(0);
const lastWsTagUpdateKey = ref(0);
const lastWsDescriptionUpdateKey = ref(0);
const preserveScrollOnNextFetch = ref(false);
const pendingScrollTop = ref(null);
const skipNextWsRefresh = ref(false);
const pauseGridAutoUpdates = ref(false);
const pendingGridRefreshAfterImport = ref(false);

// ============================================================
// FACE BBOX STATE
// ============================================================
// Key to force face bbox overlay recompute.
const faceOverlayRedrawKey = ref(0);
let gridResizeObserver = null;

function triggerFaceOverlayRedraw() {
  faceOverlayRedrawKey.value++;
}

// ============================================================
// COMFYUI
// ============================================================
const comfyuiRunner = ref(null);

const comfyuiClientId = computed(
  () => comfyuiRunner.value?.clientId?.value ?? null,
);
const comfyuiProgress = computed(
  () =>
    comfyuiRunner.value?.progress ?? {
      visible: false,
      status: "idle",
      percent: 0,
      message: "",
    },
);
const comfyuiProgressPercent = computed(() => {
  const p = comfyuiProgress.value;
  return Math.min(100, Math.max(0, Math.round(Number(p?.percent) || 0)));
});

function handleComfyuiRun(payload) {
  comfyuiRunner.value?.handleComfyuiRun(payload);
}

// ── Remix ("Generate variants…") ─────────────────────────────────────────
// Acts on the RIGHT-CLICKED picture, not the selection - the dialog discloses
// that and offers a route to the batch panel when a wider selection is live.
const remixDialogOpen = ref(false);
const remixImage = ref(null);

function openRemixDialog(pictureId) {
  const id = pictureId ?? contextMenuImage.value?.id;
  if (id == null) return;
  const image =
    allGridImages.value.find((img) => String(img?.id) === String(id)) ||
    contextMenuImage.value;
  if (!image) return;
  remixImage.value = image;
  remixDialogOpen.value = true;
}

async function runComfyuiOnGridImages({
  workflowName,
  caption = "",
  seedMode = "random",
  seed = 0,
} = {}) {
  if (!workflowName || !props.backendUrl) return;
  try {
    // Build view context so the generated picture is assigned to the current
    // set / project / character automatically.
    const contextSetId = primarySelectedSetId.value ?? undefined;
    const contextProjectId =
      projectStore.selectedProjectId != null
        ? projectStore.selectedProjectId
        : undefined;
    const rawChar = selectionStore.selectedCharacter;
    const specialIds = [
      ALL_PICTURES_ID,
      UNASSIGNED_PICTURES_ID,
      SCRAPHEAP_PICTURES_ID,
    ].map((v) => String(v ?? "").toUpperCase());
    const charNum =
      rawChar != null && !specialIds.includes(String(rawChar).toUpperCase())
        ? Number(rawChar)
        : NaN;
    const contextCharacterId =
      Number.isFinite(charNum) && charNum > 0 ? charNum : undefined;

    const payload = {
      workflow_name: workflowName,
      caption: caption || "",
      client_id: comfyuiClientId.value || undefined,
      seed_mode: seedMode,
      seed: seedMode === "fixed" ? seed : undefined,
      source_picture_id:
        selectedImageIds.value.length === 1
          ? selectedImageIds.value[0]
          : undefined,
      set_id: contextSetId,
      project_id: contextProjectId,
      character_id: contextCharacterId,
    };
    const body = await runTextToImage(payload);
    const prompts = Array.isArray(body?.prompts) ? body.prompts : [];
    handleComfyuiRun({ prompts });
  } catch (err) {
    console.error("ComfyUI T2I run failed:", err);
  }
}

function onComfyuiRefreshGrid() {
  // The new grid card for an in-app ComfyUI result now arrives via the
  // origin-aware WebSocket `picture_imported` insert (useGridRealtimeSync →
  // insertGridImagesById), so this no longer triggers a full grid refetch and
  // the old "pops in → disappears → comes back" flicker is gone. The runner
  // still fires this on completion/retry; we use it only to reconcile an OPEN
  // overlay (i2i/upscale) to the freshly-stacked output. maybeRefreshOverlayForComfyui
  // is a guarded no-op when the overlay is closed or no comfyui refresh is pending.
  void maybeRefreshOverlayForComfyui();
}

function getNowMs() {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function clampSmartScoreEstimate(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value)) return SORT_PROGRESS_ESTIMATE_DEFAULT_MS;
  return Math.max(
    SORT_PROGRESS_ESTIMATE_MIN_MS,
    Math.min(SORT_PROGRESS_ESTIMATE_MAX_MS, value),
  );
}

function easeEstimatedProgress(ratio) {
  const x = Math.max(0, Math.min(1, ratio));
  return 1 - Math.pow(1 - x, 1.35);
}

function stopSmartScoreProgressTimer() {
  if (smartScoreProgressTimer) {
    clearInterval(smartScoreProgressTimer);
    smartScoreProgressTimer = null;
  }
}

function setSmartScoreProgressPercent(
  nextPercent,
  { allowReset = false } = {},
) {
  const parsed = Number(nextPercent);
  const clamped = Number.isFinite(parsed)
    ? Math.max(0, Math.min(100, parsed))
    : 0;
  if (allowReset) {
    smartScoreProgress.percent = clamped;
    return;
  }
  // Monotonic guard: progress must never move backwards within a run.
  smartScoreProgress.percent = Math.max(smartScoreProgress.percent, clamped);
}

function startSmartScoreProgress(loadId, sortKey) {
  stopSmartScoreProgressTimer();
  const now = getNowMs();
  const incomingSortKey = String(sortKey || "").toUpperCase();
  const sameSortAsCurrent =
    smartScoreProgress.visible &&
    incomingSortKey &&
    incomingSortKey === smartScoreProgressSortKey.value;
  const rapidRestart =
    sameSortAsCurrent &&
    now - smartScoreProgressStartedAt <= SORT_PROGRESS_WARM_RESTART_WINDOW_MS;

  smartScoreProgressLoadId = Number(loadId) || 0;
  smartScoreProgressSortKey.value = incomingSortKey;
  smartScoreProgress.visible = true;
  smartScoreProgress.status = "running";

  const estimateMs = clampSmartScoreEstimate(
    sortEstimatedDurationMsByKey[smartScoreProgressSortKey.value] ??
      getSortEstimateDefaultMs(smartScoreProgressSortKey.value),
  );

  if (rapidRestart) {
    // Keep current progress on quick follow-up fetches so the bar does not
    // visually bounce backwards at startup when multiple refreshes compete.
    const currentRatio = Math.max(
      0,
      Math.min(1, smartScoreProgress.percent / SORT_PROGRESS_MAX_BEFORE_DONE),
    );
    smartScoreProgressStartedAt = now - estimateMs * currentRatio;
  } else {
    smartScoreProgressStartedAt = now;
    setSmartScoreProgressPercent(0, { allowReset: true });
  }

  smartScoreProgress.message =
    searchStore.searchQuery && searchStore.searchQuery.trim()
      ? "Searching"
      : `Sorting by ${getSortProgressLabel(smartScoreProgressSortKey.value)}`;
  smartScoreProgressTimer = setInterval(() => {
    if (!smartScoreProgress.visible) {
      stopSmartScoreProgressTimer();
      return;
    }
    const elapsed = Math.max(0, getNowMs() - smartScoreProgressStartedAt);
    const ratio = elapsed / Math.max(1, estimateMs);
    const smooth = easeEstimatedProgress(ratio);
    const next = Math.min(SORT_PROGRESS_MAX_BEFORE_DONE, smooth * 100);
    setSmartScoreProgressPercent(next);
  }, 120);
}

function completeSmartScoreProgress(loadId, measuredDurationMs, wasSuccessful) {
  if (Number(loadId) !== smartScoreProgressLoadId) return;
  stopSmartScoreProgressTimer();
  if (wasSuccessful) {
    const measured = Number(measuredDurationMs);
    if (Number.isFinite(measured) && measured > 0) {
      const sortKey = String(smartScoreProgressSortKey.value || "");
      const previous = clampSmartScoreEstimate(
        sortEstimatedDurationMsByKey[sortKey] ??
          getSortEstimateDefaultMs(sortKey),
      );
      const nextEstimate =
        (1 - SORT_PROGRESS_EWMA_ALPHA) * previous +
        SORT_PROGRESS_EWMA_ALPHA * clampSmartScoreEstimate(measured);
      sortEstimatedDurationMsByKey[sortKey] =
        clampSmartScoreEstimate(nextEstimate);
      console.debug(
        `[SortProgress] sort=${sortKey || "(none)"} total=${Math.round(measured)}ms estimate=${Math.round(sortEstimatedDurationMsByKey[sortKey] || previous)}ms`,
      );
    }
    setSmartScoreProgressPercent(100);
    smartScoreProgress.status = "completed";
    smartScoreProgress.message =
      searchStore.searchQuery && searchStore.searchQuery.trim()
        ? "Search complete"
        : `Sorted by ${getSortProgressLabel(smartScoreProgressSortKey.value)}`;
    setTimeout(() => {
      if (Number(loadId) !== smartScoreProgressLoadId) return;
      smartScoreProgress.visible = false;
      smartScoreProgress.status = "idle";
      setSmartScoreProgressPercent(0, { allowReset: true });
      smartScoreProgress.message = "Calculating smart scores";
      smartScoreProgressSortKey.value = "";
    }, SORT_PROGRESS_COMPLETION_HOLD_MS);
    return;
  }
  // Deliberately not "failed". This branch is reached for a superseded fetch
  // as often as for a real error (useGridFetch passes wasSuccessful false
  // whenever `lastRequestId` has moved on), and announcing a failure every
  // time a user re-sorts quickly is a worse lie than saying nothing.
  smartScoreProgress.status = "idle";
  smartScoreProgress.visible = false;
  setSmartScoreProgressPercent(0, { allowReset: true });
  smartScoreProgress.message = "Calculating smart scores";
  smartScoreProgressSortKey.value = "";
}

async function maybeRefreshOverlayForComfyui() {
  await comfyuiRunner.value?.maybeRefreshOverlayForComfyui();
}

// ============================================================
// THUMBNAIL INFO TEXT-FITTING
// ============================================================
function buildThumbnailInfoKey(imageId, infoKey) {
  return `${imageId}-${infoKey}`;
}

function measureTextWidth(text, el) {
  if (!textMeasureContext || !el) return 0;
  const font = getInfoFont(el);
  if (font) {
    textMeasureContext.font = font;
  }
  return textMeasureContext.measureText(text).width;
}

function truncateTextToFit(fullText, el) {
  if (!fullText || !el) return "";
  const maxWidth = el.clientWidth || 0;
  if (!maxWidth) return fullText;
  if (measureTextWidth(fullText, el) <= maxWidth) return fullText;
  const words = fullText.split(/\s+/).filter(Boolean);
  if (!words.length) return "";
  let current = "";
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (measureTextWidth(next, el) <= maxWidth) {
      current = next;
    } else {
      break;
    }
  }
  return current || words[0] || "";
}

function updateThumbnailInfoDisplay(key, fullText, el) {
  if (!el) return;
  const truncated = truncateTextToFit(fullText, el);
  if (truncated && truncated !== fullText) {
    thumbnailInfoDisplayMap[key] = truncated;
    thumbnailInfoTitleMap[key] = fullText;
  } else {
    thumbnailInfoDisplayMap[key] = fullText || "";
    if (thumbnailInfoTitleMap[key]) {
      delete thumbnailInfoTitleMap[key];
    }
  }
}

function setThumbnailInfoRef(imageId, infoKey, fullText, el) {
  const key = buildThumbnailInfoKey(imageId, infoKey);
  if (el) {
    thumbnailInfoRefs[key] = el;
    thumbnailInfoFullMap[key] = fullText || "";
    updateThumbnailInfoDisplay(key, fullText || "", el);
  } else {
    delete thumbnailInfoRefs[key];
    delete thumbnailInfoFullMap[key];
    delete thumbnailInfoDisplayMap[key];
    if (thumbnailInfoTitleMap[key]) {
      delete thumbnailInfoTitleMap[key];
    }
  }
}

function getThumbnailInfoTitle(imageId, infoKey) {
  const key = buildThumbnailInfoKey(imageId, infoKey);
  return thumbnailInfoTitleMap[key] || "";
}

function getThumbnailInfoDisplayText(imageId, infoKey, fallbackText) {
  const key = buildThumbnailInfoKey(imageId, infoKey);
  return thumbnailInfoDisplayMap[key] ?? fallbackText ?? "";
}

function handleThumbnailInfoMouseEnter(imageId, infoKey) {
  const key = buildThumbnailInfoKey(imageId, infoKey);
  const el = thumbnailInfoRefs[key];
  if (!el) return;
  updateThumbnailInfoDisplay(key, thumbnailInfoFullMap[key] || "", el);
}

function refreshAllThumbnailInfoDisplays() {
  for (const key of Object.keys(thumbnailInfoRefs)) {
    const el = thumbnailInfoRefs[key];
    const fullText = thumbnailInfoFullMap[key];
    if (!el || fullText == null) continue;
    updateThumbnailInfoDisplay(key, fullText, el);
  }
}

let initialFetchTimer = null;

onMounted(() => {
  window.addEventListener("resize", triggerFaceOverlayRedraw);
  window.addEventListener("drop", clearGridDragOverlay, true);
  window.addEventListener("dragend", clearGridDragOverlay, true);
  window.addEventListener("keydown", handleKeyDown);

  // Restore overlay from URL on initial page load (e.g. after a page refresh).
  const overlayIdFromUrl = _overlayRoute.query.overlay;
  if (overlayIdFromUrl && !overlayOpen.value) {
    openOverlay({ id: overlayIdFromUrl });
  }

  fetchAvailablePlugins();
  fetchTaggerPlugins();
  fetchAllPicturesCount();
  initGuestSession();
  const mountFetchKey = buildGridFetchKey();
  if (!hasLoadedOnce.value && !imagesLoading.value) {
    if (
      !Array.isArray(allGridImages.value) ||
      allGridImages.value.length === 0
    ) {
      if (initialFetchTimer) {
        clearTimeout(initialFetchTimer);
      }
      initialFetchTimer = setTimeout(() => {
        initialFetchTimer = null;
        const currentKey = buildGridFetchKey();
        if (currentKey !== mountFetchKey) {
          return;
        }
        if (hasLoadedOnce.value || imagesLoading.value) {
          return;
        }
        if (
          !Array.isArray(allGridImages.value) ||
          allGridImages.value.length === 0
        ) {
          fetchAllGridImages().then(() => {
            updateVisibleThumbnails();
          });
        }
      }, 80);
    }
  }
  nextTick(() => {
    updateRowHeightFromGrid();
    if (typeof ResizeObserver !== "undefined" && gridContainer.value) {
      gridResizeObserver = new ResizeObserver(() => {
        updateRowHeightFromGrid();
      });
      gridResizeObserver.observe(gridContainer.value);
    }
  });
});

watch(
  () => props.backendUrl,
  () => {
    fetchAvailablePlugins();
  },
);

onUnmounted(() => {
  window.removeEventListener("resize", triggerFaceOverlayRedraw);
  window.removeEventListener("drop", clearGridDragOverlay, true);
  window.removeEventListener("dragend", clearGridDragOverlay, true);
  window.removeEventListener("keydown", handleKeyDown);
  if (gridResizeObserver) {
    gridResizeObserver.disconnect();
    gridResizeObserver = null;
  }
  if (initialFetchTimer) {
    clearTimeout(initialFetchTimer);
    initialFetchTimer = null;
  }
  fullImagePrefetchControllers.clear();
  prefetchedFullImageIds.clear();
  prefetchedFullImageOrder.length = 0;
  if (emptyStateDelayTimer) {
    clearTimeout(emptyStateDelayTimer);
    emptyStateDelayTimer = null;
  }
  for (const timer of recentlyAddedTimers.values()) {
    clearTimeout(timer);
  }
  recentlyAddedTimers.clear();
  recentlyAddedIds.value = {};
  if (pluginProgressHideTimer) {
    clearTimeout(pluginProgressHideTimer);
    pluginProgressHideTimer = null;
  }
  stopSmartScoreProgressTimer();
  if (wsTagFullRefreshTimer) {
    clearTimeout(wsTagFullRefreshTimer);
    wsTagFullRefreshTimer = null;
  }
});

watch(
  () => gridStore.wsUpdateKey,
  (nextKey) => {
    if (!nextKey || nextKey === lastWsUpdateKey.value) return;
    lastWsUpdateKey.value = nextKey;
    if (pauseGridAutoUpdates.value) {
      pendingGridRefreshAfterImport.value = true;
      return;
    }
    const scrollTop = scrollWrapper.value?.scrollTop ?? 0;
    const threshold = rowHeight.value * 0.5;
    if (scrollTop > threshold) {
      skipNextWsRefresh.value = true;
      preserveScrollOnNextFetch.value = false;
      return;
    }
    highlightNextFetch.value = true;
    preserveScrollOnNextFetch.value = true;
  },
);

watch(
  () => wsStore.wsTagUpdate,
  (payload) => {
    if (!payload || typeof payload !== "object") return;
    const nextKey = payload.key || 0;
    if (!nextKey || nextKey === lastWsTagUpdateKey.value) return;
    lastWsTagUpdateKey.value = nextKey;
    const pictureIds = Array.isArray(payload.pictureIds)
      ? payload.pictureIds
      : [];
    // Only refresh the grid when a tag filter is active - without a filter,
    // tagging doesn't change anything visible in the grid (thumbnails and sort
    // order are unaffected), so refreshing just hammers the DB for no benefit.
    if (
      !(filterStore.tagFilter && filterStore.tagFilter.length) &&
      !(
        filterStore.tagRejectedFilter && filterStore.tagRejectedFilter.length
      ) &&
      !(
        filterStore.tagConfidenceAboveFilter &&
        filterStore.tagConfidenceAboveFilter.length
      ) &&
      !(
        filterStore.tagConfidenceBelowFilter &&
        filterStore.tagConfidenceBelowFilter.length
      )
    )
      return;
    if (pauseGridAutoUpdates.value) {
      pendingGridRefreshAfterImport.value = true;
      return;
    }
    if (overlayOpen.value) {
      // A tag edit under an active tag filter would re-query and drop the
      // now-non-matching picture from the grid mid-view (the streaming refetch
      // replaces allGridImages). Defer the reconcile until the overlay closes so
      // prev/next stay stable; closeOverlay() applies the filter removal in place.
      pendingOverlayGridRefresh.value = true;
      return;
    }
    if (payload.external) {
      // The tag change came from outside this tab (background tagging, or
      // another owner tab). Don't reshuffle the user's filtered view under them:
      // raise the click-to-refresh pill, the same contract as external picture
      // changes. Only this tab's own edits refresh the filtered grid in place.
      if (pictureIds.length) emit("flag-sort-changed", pictureIds);
      return;
    }
    // Coalesce all task-driven tag updates into an infrequent full refresh to
    // avoid starving the tagger when a large grid is open.
    scheduleWsTagFullRefresh();
  },
);

watch(
  () => wsStore.wsDescriptionUpdate,
  (payload) => {
    if (!payload || typeof payload !== "object") return;
    const nextKey = payload.key || 0;
    if (!nextKey || nextKey === lastWsDescriptionUpdateKey.value) return;
    lastWsDescriptionUpdateKey.value = nextKey;
    const pictureIds = Array.isArray(payload.pictureIds)
      ? payload.pictureIds
      : [];
    if (!pictureIds.length) return;
    for (const id of pictureIds) {
      refreshGridImage(id);
    }
  },
);

watch(
  () => wsStore.wsPluginProgress,
  (wrapped) => {
    if (!wrapped || typeof wrapped !== "object") return;
    const payload = wrapped.payload;
    if (!payload || typeof payload !== "object") return;

    if (pluginProgressHideTimer) {
      clearTimeout(pluginProgressHideTimer);
      pluginProgressHideTimer = null;
    }

    const pluginName = String(payload.plugin || "plugin").toLowerCase();
    if (pluginName === "smart_score") {
      // Smart score overlay is driven by local fetch instrumentation,
      // not websocket events, to avoid jitter/out-of-order updates.
      return;
    }
    if (pluginName === "comfyui") {
      // ComfyUI has its own dedicated runner banner; suppress duplicate
      // generic plugin overlay to avoid showing two concurrent error banners.
      pluginProgress.visible = false;
      return;
    }

    pluginProgress.runId = String(payload.run_id || pluginProgress.runId || "");
    pluginProgress.status = String(payload.status || "running");
    pluginProgress.current = Math.max(0, Number(payload.current || 0));
    pluginProgress.total = Math.max(
      pluginProgress.current,
      Number(payload.total || pluginProgress.total || 0),
    );
    const explicitProgress = Number(payload.progress);
    if (Number.isFinite(explicitProgress)) {
      pluginProgress.percent = explicitProgress;
    } else if (pluginProgress.total > 0) {
      pluginProgress.percent =
        (pluginProgress.current / pluginProgress.total) * 100;
    }
    const pluginNameForMessage = String(payload.plugin || "plugin");
    pluginProgress.message = normalizePluginProgressMessage(
      payload.message,
      `${pluginNameForMessage}: ${pluginProgress.status}`,
    );
    pluginProgress.visible = true;

    if (
      pluginProgress.status === "completed" ||
      pluginProgress.status === "failed"
    ) {
      pluginProgressHideTimer = setTimeout(() => {
        pluginProgress.visible = false;
        pluginProgressHideTimer = null;
      }, 1800);
    }
  },
);

function triggerNewImageHighlight(ids) {
  ids.forEach((id) => {
    if (!id) return;
    if (recentlyAddedTimers.has(id)) {
      clearTimeout(recentlyAddedTimers.get(id));
      recentlyAddedTimers.delete(id);
    }
    recentlyAddedIds.value[id] = true;
    const timeout = setTimeout(() => {
      recentlyAddedIds.value[id] = false;
      recentlyAddedTimers.delete(id);
    }, 2200);
    recentlyAddedTimers.set(id, timeout);
  });
}

function isImageRecentlyAdded(id) {
  return Boolean(id && recentlyAddedIds.value[id]);
}

// ============================================================
// THUMBNAIL HELPERS
// ============================================================
// Thumbnails whose <img> fired @error (e.g. an undecodable source, #585):
// the browser's native broken-image glyph is replaced with a centered icon
// until a later successful load clears the id again.
const failedThumbnailIds = reactive(new Set());

function onThumbnailLoad(id) {
  failedThumbnailIds.delete(id);
  thumbnailLoadedMap[id] = (thumbnailLoadedMap[id] || 0) + 1;
  const assignedAt = Number(thumbnailAssignedAtMap[id]);
  if (Number.isFinite(assignedAt) && assignedAt > 0) {
    delete thumbnailAssignedAtMap[id];
  }
  clearThumbnailRetry(id);
}

function clearThumbnailRetry(id) {
  if (!id) return;
  const timer = thumbnailRetryTimers.get(id);
  if (timer) {
    clearTimeout(timer);
  }
  thumbnailRetryTimers.delete(id);
}

function scheduleThumbnailRetry(id, index, requestEpoch) {
  if (!id || index == null) return;
  if ((thumbnailRetryCounts[id] || 0) >= THUMBNAIL_RETRY_LIMIT) return;
  if (thumbnailRetryTimers.has(id)) return;
  const timer = setTimeout(() => {
    thumbnailRetryTimers.delete(id);
    if (requestEpoch !== thumbnailRequestEpoch.value) return;
    const current = allGridImages.value[index];
    if (!current || current.id !== id) return;
    if (current.thumbnail) return;
    thumbnailRetryCounts[id] = (thumbnailRetryCounts[id] || 0) + 1;
    invalidateThumbnailIndex(index);
    fetchThumbnailsBatch(index, index + 1, {
      reason: "retry-missing-thumbnail",
      triggerId: id,
    });
  }, THUMBNAIL_RETRY_DELAY_MS);
  thumbnailRetryTimers.set(id, timer);
}

function setThumbnailRef(id, el) {
  if (el) {
    thumbnailRefs[id] = el;
    if (!thumbnailReadyMap[id]) {
      thumbnailReadyMap[id] = true;
    }
  } else {
    delete thumbnailRefs[id];
    if (thumbnailReadyMap[id]) {
      delete thumbnailReadyMap[id];
    }
  }
}

const _makeRefSetter = (map) => (id, el) => {
  if (el) {
    map[id] = el;
  } else {
    delete map[id];
  }
};
const setDragPreviewRef = _makeRefSetter(dragPreviewRefs);
const setThumbnailContainerRef = _makeRefSetter(thumbnailContainerRefs);

function isThumbnailReady(id) {
  return Boolean(id && thumbnailReadyMap[id]);
}

function getThumbnailSrc(img) {
  if (!img) return null;
  return img.thumbnail || null;
}

function getVideoThumbnailSrc(img) {
  if (!img || !isVideo(img)) return null;
  if (!img.id || !img.format) return null;
  // Build a stable URL without the pixel_sha cache-buster so the browser treats
  // this as the same resource as the overlay's videoSrc (which also omits it).
  // Using buildMediaUrl here would add ?v=pixel_sha, causing two concurrent
  // requests to different URLs for the same file - the browser aborts one,
  // leaving the overlay's <video> element stuck loading.
  return `${props.backendUrl}/pictures/${img.id}.${img.format.toLowerCase()}`;
}

// ============================================================
// FACE BBOX FUNCTIONS
// ============================================================
// selectedFaceIds, isFaceSelected, toggleFaceSelection, clearFaceSelection,
// onFaceBboxDragStart - moved to useMultiSelect composable.

// Square-crop render helpers (thumbnail v2). Square mode sprite-crops the whole
// AR bitmap to the stored face-weighted rectangle; justified mode shows the
// whole bitmap. Videos keep object-fit:cover (the crop rectangle describes the
// still bitmap, not the video frames), and a picture whose crop fields have not
// yet been populated falls back to cover centring until they arrive.
function isSquareCropActive(img) {
  return (
    !isJustifiedMode.value && !isVideo(img) && squareCropParams(img) !== null
  );
}

// Inline <img> style for the sprite crop, or null → CSS object-fit:cover.
function getSquareCropImgStyle(img) {
  if (isJustifiedMode.value || isVideo(img)) return null;
  return squareCropImgStyle(img);
}

// Helper to calculate face/detection bbox overlay style. Square-crop mode maps
// the bitmap-space bbox through the stored crop; otherwise it mirrors the
// object-fit:cover render (justified whole-bitmap, or the square-mode fallback).
function getFaceBboxStyle(bbox, idx, img, el, isSelected) {
  if (!el) return { display: "none" };
  const container = el.parentElement;
  if (!container) return { display: "none" };
  const containerWidth = container.clientWidth;
  const containerHeight = container.clientHeight;
  const naturalWidth = img.thumbnail_width || img.width || 1;
  const naturalHeight = img.thumbnail_height || img.height || 1;
  const cropParams = isSquareCropActive(img) ? squareCropParams(img) : null;
  const rect = cropParams
    ? squareCropBboxRect(bbox, cropParams, containerWidth)
    : coverBboxRect(
        bbox,
        naturalWidth,
        naturalHeight,
        containerWidth,
        containerHeight,
      );
  const { left, top, width, height } = rect;
  const borderColor = faceBoxColor(idx);
  return {
    position: "absolute",
    border: `${isSelected ? 3 : 1.5}px solid ${borderColor}`,
    background: `${borderColor}${isSelected ? "44" : "22"}`,
    "--face-frame-color": `${borderColor}${isSelected ? "cc" : "aa"}`,
    left: `${left}px`,
    top: `${top}px`,
    width: `${width}px`,
    height: `${height}px`,
    pointerEvents: "auto",
    zIndex: isSelected ? 60 : 40,
    display: "block",
  };
}

function getFaceBboxOverlays(img) {
  void faceOverlayRedrawKey.value; // depend on redraw key
  void selectedFaceIds.value;
  void thumbnailReadyMap[img.id];
  if (
    !gridStore.showFaceBboxes ||
    !img.faces ||
    !img.faces.length ||
    !(img.thumbnail_width || img.width) ||
    !(img.thumbnail_height || img.height)
  ) {
    return [];
  }
  const el = thumbnailRefs[img.id];
  if (!el) return [];
  const firstFrameFaces = img.faces
    .map((face, faceIdx) => ({ face, faceIdx }))
    .filter((entry) => entry.face.frame_index === 0);
  return firstFrameFaces.map((entry, colorIdx) => ({
    style: getFaceBboxStyle(
      entry.face.bbox,
      colorIdx,
      img,
      el,
      isFaceSelected(img.id, entry.faceIdx),
    ),
    faceIdx: entry.faceIdx,
    faceId: entry.face.id,
    face: entry.face,
    colorIdx,
  }));
}

// Detection (object) bbox overlays - mirror getFaceBboxOverlays, but colour
// each box by its label so boxes sharing a label share a colour.
function getDetectionBboxOverlays(img) {
  void faceOverlayRedrawKey.value; // depend on redraw key
  void thumbnailReadyMap[img.id];
  if (
    !gridStore.showDetections ||
    !img.detections ||
    !img.detections.length ||
    !(img.thumbnail_width || img.width) ||
    !(img.thumbnail_height || img.height)
  ) {
    return [];
  }
  const el = thumbnailRefs[img.id];
  if (!el) return [];
  const labelColorIndex = new Map();
  const firstFrameDetections = img.detections
    .map((det, detIdx) => ({ det, detIdx }))
    .filter((entry) => (entry.det.frame_index ?? 0) === 0);
  return firstFrameDetections.map((entry) => {
    const label = entry.det.label ?? "";
    if (!labelColorIndex.has(label)) {
      labelColorIndex.set(label, labelColorIndex.size);
    }
    const colorIdx = labelColorIndex.get(label);
    return {
      style: getFaceBboxStyle(entry.det.bbox, colorIdx, img, el, false),
      detIdx: entry.detIdx,
      detId: entry.det.id,
      det: entry.det,
      colorIdx,
      color: faceBoxColor(colorIdx),
    };
  });
}

// Track which image is currently hovered
// ============================================================
// HOVER STATE + THUMBNAIL INFO DISPLAY ITEMS
// ============================================================
const hoveredImageIdx = ref(null);
const hoveredStackId = ref(null);

function handleImageMouseEnter(img) {
  // `inert` already blocks the event in supporting browsers; this is the
  // testable half of the same rule. `hoveredImageIdx` is what the digit-scoring
  // shortcut acts on when nothing is selected, so a ghost must never be hovered.
  if (isImageGhosted(img)) return;
  hoveredImageIdx.value = img.idx;
  hoveredStackId.value = getPictureStackId(img) ?? null;
}
function handleImageMouseLeave(img) {
  if (hoveredImageIdx.value === img.idx) hoveredImageIdx.value = null;
  if (hoveredStackId.value && getPictureStackId(img) === hoveredStackId.value) {
    hoveredStackId.value = null;
  }
}

// Number of images before/after viewport to load thumbnails for
// Format date to ISO (YYYY-MM-DD HH:mm:ss)
function getThumbnailInfoItems(img) {
  if (!img) return [];
  const items = [];
  const selectedSort =
    typeof sortStore.selectedSort === "string" ? sortStore.selectedSort : "";

  if (
    selectedSort.includes("CHARACTER_LIKENESS") &&
    img.character_likeness !== undefined
  ) {
    items.push({
      key: "character_likeness",
      text: `Likeness: ${img.character_likeness.toFixed(2)}`,
    });
  }

  const smartScore = getGridSmartScoreValue(img);
  if (selectedSort.includes("SMART_SCORE") && smartScore !== null) {
    items.push({
      key: "smart_score",
      text: `Smart Score: ${smartScore.toFixed(2)}`,
    });
  }

  if (selectedSort === "TEXT_CONTENT" && typeof img.text_score === "number") {
    items.push({
      key: "text_score",
      text: `Text: ${(img.text_score * 100).toFixed(0)}%`,
    });
  }

  if (
    typeof searchStore.searchQuery === "string" &&
    img.likeness_score !== undefined
  ) {
    items.push({
      key: "search_likeness",
      text: `Search likeness: ${img.likeness_score.toFixed(2)}`,
    });
  } else if (selectedSort === "IMPORTED_AT" && img.imported_at) {
    items.push({
      key: "imported_at",
      text: formatUserDate(img.imported_at, userPrefsStore.dateFormat),
    });
  } else if (selectedSort.includes("DATE") && img.created_at) {
    items.push({
      key: "created_at",
      text: formatUserDate(img.created_at, userPrefsStore.dateFormat),
    });
  } else if (
    selectedSort === LIKENESS_GROUPS_SORT_KEY &&
    (typeof img.stackIndex === "number" || typeof img.stack_index === "number")
  ) {
    if (!gridStore.showStacks) {
      return items;
    }
    const stackIndex =
      typeof img.stackIndex === "number" ? img.stackIndex : img.stack_index;
    items.push({
      key: "stack_index",
      text: `Group ${stackIndex + 1}`,
    });
  }
  return items;
}

function formatCompactDate(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr;
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  const fmt =
    typeof userPrefsStore.dateFormat === "string"
      ? userPrefsStore.dateFormat
      : "locale";
  const y = d.getFullYear();
  const day = d.getDate();
  const MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  const mon = MONTHS[d.getMonth()];
  switch (fmt) {
    case "eu":
    case "british":
    case "iso":
      return sameYear ? `${day} ${mon}` : `${day} ${mon} ${y}`;
    case "us":
      return sameYear ? `${mon} ${day}` : `${mon} ${day}, ${y}`;
    case "ymd-slash":
    case "ymd-dot":
      return sameYear ? `${mon} ${day}` : `${y} ${mon} ${day}`;
    case "ymd-jp":
      return sameYear
        ? `${d.getMonth() + 1}月${day}日`
        : `${y}年${d.getMonth() + 1}月${day}日`;
    case "locale":
    default:
      return d.toLocaleDateString(
        undefined,
        sameYear
          ? { month: "short", day: "numeric" }
          : { year: "numeric", month: "short", day: "numeric" },
      );
  }
}

function formatCompactDatetime(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr;
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  const fmt =
    typeof userPrefsStore.dateFormat === "string"
      ? userPrefsStore.dateFormat
      : "locale";
  const y = d.getFullYear();
  const day = d.getDate();
  const MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  const mon = MONTHS[d.getMonth()];
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const time24 = `${hh}:${mm}`;
  function ampmTime() {
    let h = d.getHours();
    const ampm = h >= 12 ? "PM" : "AM";
    h = h % 12 || 12;
    return `${h}:${mm} ${ampm}`;
  }
  switch (fmt) {
    case "eu":
    case "british":
    case "iso":
      return sameYear
        ? `${day} ${mon} ${time24}`
        : `${day} ${mon} ${y} ${time24}`;
    case "us":
      return sameYear
        ? `${mon} ${day} ${ampmTime()}`
        : `${mon} ${day}, ${y} ${ampmTime()}`;
    case "ymd-slash":
    case "ymd-dot":
      return sameYear
        ? `${mon} ${day} ${time24}`
        : `${y} ${mon} ${day} ${time24}`;
    case "ymd-jp":
      return sameYear
        ? `${d.getMonth() + 1}月${day}日 ${time24}`
        : `${y}年${d.getMonth() + 1}月${day}日 ${time24}`;
    case "locale":
    default:
      return d.toLocaleString(
        undefined,
        sameYear
          ? {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            }
          : {
              year: "numeric",
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            },
      );
  }
}

// ── Visible range label (emitted to SelectionBar) ──────────────
function getImageSortLabel(img) {
  if (!img) return null;
  const isSearchMode = !!(
    searchStore.searchQuery && searchStore.searchQuery.trim()
  );
  const sort =
    typeof sortStore.selectedSort === "string" ? sortStore.selectedSort : "";
  if (isSearchMode && typeof img.likeness_score === "number")
    return `≈ ${img.likeness_score.toFixed(2)}`;
  if (sort === "IMPORTED_AT" && img.imported_at)
    return formatCompactDatetime(img.imported_at);
  if (sort.includes("DATE") && img.created_at)
    return formatCompactDate(img.created_at);
  const smartScore = getGridSmartScoreValue(img);
  if (sort.includes("SMART_SCORE") && smartScore !== null)
    return `★ ${(Math.round(smartScore * 10) / 10).toFixed(1)}`;
  if (
    sort.includes("CHARACTER_LIKENESS") &&
    typeof img.character_likeness === "number"
  )
    return `≈ ${(img.character_likeness * 100).toFixed(0)}%`;
  if (sort === "TEXT_CONTENT" && typeof img.text_score === "number")
    return `${(img.text_score * 100).toFixed(0)}%`;
  if (sort === "TAG_UNCERTAINTY" && typeof img.tag_uncertainty === "number")
    return `${(img.tag_uncertainty * 100).toFixed(0)}%`;
  if (
    sort === "ANOMALY_TAG_UNCERTAINTY" &&
    typeof img.anomaly_tag_uncertainty === "number"
  )
    return `${(img.anomaly_tag_uncertainty * 100).toFixed(0)}%`;
  if (sort === "SCORE" && typeof img.score === "number")
    return `★ ${img.score}`;
  return null;
}

const visibleRangeLabel = computed(() => {
  const images = allGridImages.value;
  if (!images.length) return null;
  const firstImg = images[visibleStart.value] ?? images[0];
  const lastImg = images[Math.max(0, (visibleEnd.value || 1) - 1)];
  const first = getImageSortLabel(firstImg);
  if (!first) return null;
  const last =
    lastImg && lastImg !== firstImg ? getImageSortLabel(lastImg) : null;
  if (!last || last === first) return first;
  return `${first} – ${last}`;
});

const tasksStore = useTasksStore();
const reviewSessionsStore = useReviewSessionsStore();
const lockedSetsStore = useLockedSetsStore();
const genStackPrefs = useGenStackPrefsStore();
const scrapheapRetentionStore = useScrapheapRetentionStore();
const operationStore = useOperationStore();
// Every failure path in this component reports through the notice surface. A
// native alert() is unstyled, blocking and focus-stealing; a bare `catch` that
// only logs is worse, because the user is never told at all.
const noticeStore = useNoticeStore();

/**
 * Human-readable reason from an axios/HTTP error, for a one-sentence notice.
 * @param {unknown} err
 * @param {string} fallback - used when the server sent nothing useful.
 */
function errorDetail(err, fallback = "Please try again.") {
  // Delegates: this used to be a verbatim copy of `errorMessage`'s body under a
  // name that shadowed the `errorDetail` it meant to call, so it recursed until
  // the stack blew - and every failure path in this component reports through
  // it, which turned each of them from "a notice explaining what went wrong"
  // into a RangeError with no notice at all. Caught by a test that made a
  // rotate fail on purpose.
  return errorMessage(err, fallback);
}
// Live "is the Review Sessions overlay up" signal. It stays mounted over the
// grid as a modal review surface with its own keyboard/drag handling, so the
// grid's keyboard shortcuts and native drag must go inert while it is open.
// Authoritative source: the store ref the Toolbar sets true and App.vue's
// close handler sets false (NOT the retired local reviewOverlayOpen ref).
const reviewOverlayOpen = computed(() => reviewSessionsStore.overlayOpen);

// ── Breadcrumb (current-view path) ──────────────────────────────────────
// The trail logic lives in useBreadcrumb, shared with the desktop title bar.
// In the desktop shell the breadcrumb renders in the title bar instead, so the
// in-grid overlay below is gated on !isDesktop.
const isDesktop = typeof window !== "undefined" && !!window.pixlstashDesktop;
const { breadcrumb, navigateBreadcrumb } = useBreadcrumb();

// Below 600px the centred notice card widens over the bottom-left breadcrumb, so
// there it joins the bottom-edge contract (notice-surface.md §2.4). Above that
// width it sits outside the notice column's footprint and contributes nothing -
// which is what `narrowOnly` encodes.
const breadcrumbEl = ref(null);
useBottomAnchor("grid-breadcrumb", breadcrumbEl, { narrowOnly: true });

// The action receipt shares the selection pill's slot, so when the pill is up
// the receipt sits above it. The lift is the pill's MEASURED height plus the
// standard gap, never a constant, because the pill wraps and grows on coarse
// pointers (the 56px in floatingBottom.js is a first-frame fallback, not a
// design token).
const { height: selectionBarHeight } = useAnchorHeight("selection-bar");
const actionReceiptLift = computed(() =>
  selectionBarHeight.value > 0
    ? selectionBarHeight.value + FLOATING_BOTTOM_GAP_PX
    : 0,
);

watch(
  visibleRangeLabel,
  (label) => {
    emit("update:visible-range-label", label);
  },
  { immediate: true },
);

// Publish the total number of pictures matching the active filter/sort so the
// Filter menu header can show a live "N matches" count. allGridImages holds the
// full fetched set, so its length is the match total.
watch(
  () => allGridImages.value.length,
  (count) => {
    emit("update:match-count", count);
  },
  { immediate: true },
);
// ────────────────────────────────────────────────────────────────

function prefetchFullImage(img) {
  if (!img || !img.id) return;
  if (isVideo(img)) return;
  const id = img.id;
  if (prefetchedFullImageIds.has(id) || fullImagePrefetchControllers.has(id)) {
    return;
  }
  const url = appendShareToken(
    buildMediaUrl({ backendUrl: props.backendUrl, image: img }),
  );
  if (!url) return;
  const preloader = new Image();
  fullImagePrefetchControllers.set(id, preloader);
  preloader.onload = () => {
    fullImagePrefetchControllers.delete(id);
    prefetchedFullImageIds.add(id);
    prefetchedFullImageOrder.push(id);
    while (prefetchedFullImageOrder.length > PREFETCHED_FULL_IMAGE_LIMIT) {
      const oldest = prefetchedFullImageOrder.shift();
      if (oldest !== undefined) {
        prefetchedFullImageIds.delete(oldest);
      }
    }
  };
  preloader.onerror = () => {
    fullImagePrefetchControllers.delete(id);
  };
  preloader.decoding = "async";
  preloader.loading = "eager";
  preloader.src = url;
}

// ============================================================
// SELECTION + DRAG HELPERS
// ============================================================
function handleImageError(img, event) {
  if (img?.id != null) {
    failedThumbnailIds.add(img.id);
  }
  const imgEl = event?.target;
  if (imgEl instanceof HTMLImageElement) {
    const src = imgEl.src || "";
    if (src.endsWith(".mp4") || src.endsWith(".webm") || src.endsWith(".mov")) {
      return;
    }
    if (imgEl.dataset.errorLogged === "1") {
      return;
    }
    imgEl.dataset.errorLogged = "1";
    console.error("[ImageGrid.vue] Image load error for:", src);
  }
  const src = imgEl?.src || "";
  if (!src) {
    return;
  }
  console.error("[ImageGrid] Image load error for", src);
}

// clearSelection - moved to useMultiSelect composable.
// getDragSelectionIds/setupMultiExportDrag/prepareThumbnailNativeDrag/handleThumbnailPointerRelease - moved to useGridDragDrop composable.

// Video refs for hover play/pause in grid
// ============================================================
// VIDEO
// ============================================================
const videoRefs = {};
function setVideoRef(id, el) {
  if (el) {
    videoRefs[id] = el;
  } else {
    delete videoRefs[id];
  }
}
function playVideo(id) {
  const v = videoRefs[id];
  if (!v) return;
  // Trigger load on-demand only when hovered; do nothing if already loading/playing.
  v.preload = "auto";
  v.play().catch(() => {});
}
function pauseVideo(id) {
  const v = videoRefs[id];
  if (!v) return;
  v.pause();
  v.currentTime = 0;
  // Abort any in-progress network fetch so idle tiles consume no bandwidth.
  v.preload = "none";
  v.load();
}

// ============================================================
// GROUP / SET MEMBERSHIP
// ============================================================
async function removeFromGroup() {
  if (!selectedImageIds.value.length && !selectedFaceIds.value.length) return;
  const faceIds = selectedFaceIds.value
    .map((entry) => entry.faceId)
    .filter((id) => id !== undefined && id !== null);
  const pictureIds = selectedImageIds.value.slice();
  if (isScrapheapView.value) {
    if (!pictureIds.length) {
      clearFaceSelection();
      return;
    }
    restoreScrapheap(pictureIds)
      .catch((err) => {
        console.error("Failed to restore pictures from the scrapheap", err);
        noticeStore.error(
          `Couldn't restore those pictures. ${errorDetail(err)}`,
          { key: "scrapheap-restore-selection" },
        );
      })
      .finally(() => {
        allGridImages.value = allGridImages.value.filter(
          (img) => !pictureIds.includes(img.id),
        );
        selectedImageIds.value = [];
        clearFaceSelection();
        lastSelectedImageId.value = null;
        fetchAllGridImages().then(() => {
          loadedRanges.value = [];
          updateVisibleThumbnails();
          emit("refresh-sidebar");
        });
        updateVisibleThumbnails();
      });
    return;
  }
  // Remove from character
  if (
    selectionStore.selectedCharacter &&
    selectionStore.selectedCharacter !== ALL_PICTURES_ID &&
    selectionStore.selectedCharacter !== UNASSIGNED_PICTURES_ID
  ) {
    const requests = [];
    if (pictureIds.length) {
      requests.push(
        removeCharacterFaces(selectionStore.selectedCharacter, pictureIds),
      );
    }
    if (faceIds.length) {
      requests.push(
        removeCharacterFacesByFaceId(
          selectionStore.selectedCharacter,
          faceIds,
        ),
      );
    }
    if (!requests.length) return;
    Promise.all(requests)
      .catch((err) => {
        console.error("Failed to remove faces from character", err);
        noticeStore.error(
          `Couldn't remove those faces from the person. ${errorDetail(err)}`,
          { key: "faces-remove-character" },
        );
      })
      .finally(() => {
        if (pictureIds.length) {
          // Remove affected images from grid immediately
          allGridImages.value = allGridImages.value.filter(
            (img) => !pictureIds.includes(img.id),
          );
        }
        selectedImageIds.value = [];
        clearFaceSelection();
        lastSelectedImageId.value = null;
        fetchAllGridImages().then(() => {
          loadedRanges.value = [];
          updateVisibleThumbnails();
          emit("refresh-sidebar");
        });
        updateVisibleThumbnails();
      });
    return;
  }
  // Remove from set
  if (
    selectionStore.selectedSet &&
    selectionStore.selectedSet !== ALL_PICTURES_ID &&
    selectionStore.selectedSet !== UNASSIGNED_PICTURES_ID
  ) {
    if (!pictureIds.length) {
      clearFaceSelection();
      return;
    }

    // Build a fast lookup of id → grid image for stack info.
    const imageById = new Map(
      (allGridImages.value || [])
        .filter((img) => img && img.id != null)
        .map((img) => [String(img.id), img]),
    );

    // Classify each selected picture:
    //   No stack        → remove only that picture from the set.
    //   Collapsed stack → remove ALL members of that stack from the set;
    //                     leave the stack structure intact (the whole stack
    //                     leaves the set as an atomic unit).
    //   Expanded stack  → remove only that picture from the set AND
    //                     remove it from the stack (unstack it).
    const idsToRemoveFromSet = new Set();
    const stackRemovalsForExpanded = new Map(); // stackId → [pictureId, ...]
    const collapsedStackIds = new Set();

    for (const id of pictureIds) {
      const img = imageById.get(String(id));
      const stackId = getPictureStackId(img);
      if (!stackId) {
        idsToRemoveFromSet.add(id);
      } else if (expandedStackIds.value.has(stackId)) {
        idsToRemoveFromSet.add(id);
        const arr = stackRemovalsForExpanded.get(stackId) ?? [];
        arr.push(id);
        stackRemovalsForExpanded.set(stackId, arr);
      } else {
        collapsedStackIds.add(stackId);
      }
    }

    // For each collapsed stack fetch all member IDs so they can all be
    // removed from the set in one pass.
    for (const stackId of collapsedStackIds) {
      const cached = expandedStackMembers.value.get(stackId);
      let memberIds;
      if (cached?.ids?.length) {
        memberIds = cached.ids;
      } else {
        try {
          const stack = await getStack(stackId);
          memberIds = stack?.picture_ids ?? [];
        } catch (err) {
          // Fallback: only remove the originally-selected picture(s). That is a
          // narrower result than the user asked for (a collapsed stack tile
          // stands for the whole stack), so it is reported rather than swallowed.
          console.warn(
            `Failed to fetch members of stack ${stackId}; removing only the selected pictures from it.`,
            err,
          );
          noticeStore.warning(
            "Couldn't read a stack's members, so only the selected pictures were removed from it.",
            { key: "stack-members-fetch" },
          );
          memberIds = pictureIds.filter((id) => {
            const img = imageById.get(String(id));
            return getPictureStackId(img) === stackId;
          });
        }
      }
      for (const id of memberIds) idsToRemoveFromSet.add(id);
    }

    try {
      // Remove from picture set (all affected IDs in parallel).
      await Promise.all(
        [...idsToRemoveFromSet].map((id) =>
          removePictureFromSet(selectionStore.selectedSet, id).catch((err) => {
            // Expected when the picture was not in the set (the selection can
            // span sets); log rather than drop it so a real failure is visible.
            console.debug(
              `Could not remove picture ${id} from set ${selectionStore.selectedSet}`,
              err,
            );
          }),
        ),
      );

      // For expanded stacks: also remove the selected picture(s) from the
      // stack itself so they become standalone images.
      if (stackRemovalsForExpanded.size) {
        await Promise.all(
          [...stackRemovalsForExpanded.entries()].map(([stackId, ids]) =>
            removeStackMembers(stackId, ids).catch(
              (err) => {
                console.error("Failed to remove from stack:", err);
                // The set removal below still runs, so the user sees the
                // pictures leave the set while the stack detach silently did
                // not happen. A locked set is the one refusal nobody can
                // diagnose without being told which set froze it.
                if (isLockedRefusal(err)) {
                  const sets = lockedSetsSentence(lockedSets(err));
                  noticeStore.error(
                    sets
                      ? `They left the set, but could not leave their stack: ${sets}`
                      : "They left the set, but could not leave their stack: a locked set freezes it.",
                  );
                }
              },
            ),
          ),
        );
      }

      // Optimistic grid update: remove everything that left the set.
      const removedSet = new Set([...idsToRemoveFromSet].map(String));
      allGridImages.value = allGridImages.value.filter(
        (img) => !removedSet.has(String(img?.id)),
      );
    } catch (err) {
      console.error("Failed to remove pictures from set", err);
      noticeStore.error(
        `Couldn't remove those pictures from the set. ${errorDetail(err)}`,
        { key: "set-remove-pictures" },
      );
    }

    selectedImageIds.value = [];
    clearFaceSelection();
    lastSelectedImageId.value = null;
    await fetchAllGridImages();
    loadedRanges.value = [];
    updateVisibleThumbnails();
    emit("refresh-sidebar");
    return;
  }
}

function handleOverlayAddedToSet(payload) {
  const pictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
    : [];
  if (!pictureIds.length) return;
  const changedSetId = Number(payload?.setId);
  const action = String(payload?.action || "added");

  if (
    isSetOverlapView.value &&
    Number.isFinite(changedSetId) &&
    normalizedSelectedSetIds.value.includes(changedSetId)
  ) {
    // In overlap view, removing membership from one selected set means the
    // picture no longer belongs to the intersection and should disappear.
    if (action === "removed") {
      if (overlayOpen.value) {
        // Defer grid removal until the overlay closes so the current picture
        // doesn't vanish from the filmstrip mid-viewing.
        pendingOverlayGridRefresh.value = true;
      } else {
        removeImagesById(pictureIds);
        selectedImageIds.value = selectedImageIds.value.filter(
          (id) => !pictureIds.includes(id),
        );
        clearFaceSelection();
        lastSelectedImageId.value = null;
      }
    }
  } else if (
    hasSetSelection.value &&
    !isSetOverlapView.value &&
    action === "removed" &&
    Number.isFinite(changedSetId) &&
    changedSetId === primarySelectedSetId.value
  ) {
    if (overlayOpen.value) {
      pendingOverlayGridRefresh.value = true;
    } else {
      removeImagesById(pictureIds);
    }
  }

  if (
    selectionStore.selectedCharacter === UNASSIGNED_PICTURES_ID &&
    !hasSetSelection.value
  ) {
    if (overlayOpen.value) {
      pendingOverlayGridRefresh.value = true;
    } else {
      removeImagesById(pictureIds);
    }
  }
  emit("refresh-sidebar");
}

function handleAddToCharacter(payload) {
  const pictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
    : [];
  if (!pictureIds.length) return;
  if (
    selectionStore.selectedCharacter === UNASSIGNED_PICTURES_ID &&
    !hasSetSelection.value
  ) {
    removeImagesById(pictureIds);
    selectedImageIds.value = [];
    clearFaceSelection();
    lastSelectedImageId.value = null;
    updateVisibleThumbnails();
  }
  emit("refresh-sidebar");
}

// ── Create a person from the context menu (#645) ─────────────────────────────
// The Person flyout's "New person…" / Create "query" rows land here (via the
// menu's delegate pattern). The selection is captured at flow start
// (pendingCreatePersonAssign) so it survives the dialog; on save the captured
// selection is assigned to the new person, on cancel nothing is created or
// assigned and the grid selection is untouched.
const createPersonOpen = ref(false);
const createPersonCharacter = ref(null);
const createPersonProjects = ref([]);
let pendingCreatePersonAssign = null;

async function handleCreateCharacterFromMenu(query) {
  pendingCreatePersonAssign = chooseCharacterAssignment({
    pictureIds: selectedImageIds.value.slice(),
    faceEntries: selectedFaceIds.value.slice(),
  });
  const typed = typeof query === "string" ? query.trim() : "";
  const apiOpts = { baseUrl: props.backendUrl };
  // Projects feed the dialog's project select; the character list is only
  // needed to derive the next free default name when nothing was typed.
  const [projects, characters] = await Promise.all([
    listProjects(apiOpts).catch((e) => {
      console.warn("Couldn't list projects for the person editor", e);
      return [];
    }),
    typed
      ? Promise.resolve([])
      : listCharacters(apiOpts).catch((e) => {
          console.warn(
            "Couldn't list characters to derive a default person name",
            e,
          );
          return [];
        }),
  ]);
  createPersonProjects.value = Array.isArray(projects) ? projects : [];
  createPersonCharacter.value = {
    id: null,
    name: typed || nextFreeCharacterName(characters),
    description: "",
    extra_metadata: "",
    // Same pre-fill as SideBar.createCharacter: the active project when the
    // sidebar is in project view, otherwise none.
    project_id:
      projectStore.projectViewMode === "project"
        ? projectStore.selectedProjectId
        : null,
  };
  createPersonOpen.value = true;
}

function handleCreatePersonClose() {
  createPersonOpen.value = false;
  createPersonCharacter.value = null;
  pendingCreatePersonAssign = null;
}

async function handleCreatePersonSaved(savedCharacter) {
  createPersonOpen.value = false;
  createPersonCharacter.value = null;
  const pending = pendingCreatePersonAssign;
  pendingCreatePersonAssign = null;
  const characterId = savedCharacter?.id;
  const name = savedCharacter?.name || "person";
  if (characterId == null) {
    // The editor reported success without a usable record; the person may
    // exist server-side, but there is nothing to assign to. Surface it rather
    // than failing silently, and refresh so the sidebar shows the truth.
    // Defensive only: CharacterEditor unwraps `CharacterMutationResponse` and
    // does not emit `saved` at all unless the record has an id, so this must
    // never fire in normal operation. If it does, the payload shape is wrong.
    console.error(
      "create-person: `saved` payload carried no character id, so the " +
        "selection was not assigned. Expected the unwrapped record " +
        "{id, name, ...}; if this looks like {status, character} the " +
        "CharacterMutationResponse unwrap in CharacterEditor has regressed.",
      {
        payloadKeys:
          savedCharacter && typeof savedCharacter === "object"
            ? Object.keys(savedCharacter)
            : typeof savedCharacter,
        savedCharacter,
      },
    );
    noticeStore.error(
      `Created ${name}, but the selection couldn't be assigned. Assign it from the Person menu.`,
      { key: "create-person-assign" },
    );
    emit("refresh-sidebar");
    return;
  }
  if (!pending || pending.mode === "none") {
    noticeStore.success(`Created ${name}.`, { key: "create-person-assign" });
    emit("refresh-sidebar");
    return;
  }
  try {
    if (pending.mode === "faces") {
      await addCharacterFacesByFaceId(characterId, pending.ids);
    } else {
      await addCharacterFaces(characterId, pending.ids);
    }
    const n = pending.ids.length;
    const unit = pending.mode === "faces" ? "face" : "picture";
    noticeStore.success(
      `Created ${name}, assigned ${n} ${unit}${n === 1 ? "" : "s"}.`,
      { key: "create-person-assign" },
    );
    // Standard post-assign bookkeeping: prunes the Unassigned view when
    // relevant and emits refresh-sidebar (characters, sets, sort options).
    handleAddToCharacter({ characterId, pictureIds: pending.pictureIds });
  } catch (e) {
    console.error("Failed to assign the selection to the new person", e);
    noticeStore.error(
      `Created ${name}, but couldn't assign the selection. ${errorDetail(e)}`,
      { key: "create-person-assign" },
    );
    emit("refresh-sidebar");
  }
}

function handleRemoveFromCharacter(payload) {
  const pictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
    : [];
  if (!pictureIds.length) return;
  const removedCharId = payload?.characterId;
  const currentChar = selectionStore.selectedCharacter;
  const isInRemovedCharView =
    removedCharId != null &&
    currentChar != null &&
    String(currentChar) === String(removedCharId) &&
    currentChar !== ALL_PICTURES_ID &&
    currentChar !== UNASSIGNED_PICTURES_ID &&
    currentChar !== SCRAPHEAP_PICTURES_ID;
  if (isInRemovedCharView) {
    removeImagesById(pictureIds);
    selectedImageIds.value = [];
    clearFaceSelection();
    lastSelectedImageId.value = null;
    updateVisibleThumbnails();
  }
  emit("refresh-sidebar");
}

// ── "Frozen by a locked set" outcome ─────────────────────────────────────────
// The bulk delete skips locked pictures and reports them in `skipped_locked`.
// Surfacing that is the whole point: a 200 that deleted nothing must never look
// like success.
//
// This was a dialog only because no notice host existed. It is a `warning`
// notice now (notice-surface.md §1): it reports an outcome already committed,
// needs no consent, and enumerates nothing - so it fails all three dialog tests.
// The dialog's title + body collapse to the one sentence the surface allows,
// and its `hint` becomes the action, which routes to the locked set the user has
// to unlock.
//
// Both cards are scoped (notice-surface.md §9.6) - see `lockedDeleteNotice`,
// declared with the selection state it watches.
/**
 * Report the locked-set outcome, if there is one to report.
 * @param {{title: string, body: string, hint: string}|null} message - from
 *   `buildLockedDeleteMessage`; `null` means nothing was skipped, so stay quiet.
 */
function showLockedDeleteNotice(message) {
  if (!message) return;
  noticeStore.warning(message.body, {
    // One key for both the pre-flight block and the post-response outcome: a
    // user retrying a locked selection gets one card with a count, not a stack.
    key: "delete-skipped-locked",
    // The ordinary `warning` window, restated explicitly for one reason: to opt
    // out of the action⇒sticky default (§6 rule 1). This card reports what just
    // happened to the current selection; it is not a companion to the unlock,
    // which is several clicks deep in a sidebar context menu and will outlast
    // any sane notice. It should be long gone by then. The reading-time floor
    // still applies, and hover/focus still pauses it for a slow reader.
    timeout: DEFAULT_TIMEOUTS.warning,
    action: {
      label: "Help",
      handler: () => {
        // Follow-up carrying the lever (how to unlock), too long for the
        // one-sentence rule on the first card. No explicit window: the `info`
        // default raised by the reading-time floor is exactly the right rule for
        // a long sentence, and the same copy lives on the lock badge tooltip, so
        // nothing is lost when it goes.
        noticeStore.push({
          level: "info",
          text: message.hint,
          key: "delete-skipped-locked-help",
        });
      },
    },
  });
  // After the flush this operation's own selection edit schedules - see
  // useScopedNotice: arming any earlier would let the delete dismiss its own
  // report.
  lockedDeleteNotice.arm();
}

// `idsOverride` scopes the delete to an explicit picture list (the overlay
// context menu passes `[overlayImageId]`). When set, the grid selection is
// neither read for the target NOR mutated as a side effect - the overlay owns
// its own post-delete cleanup (it closes the lightbox). Default (null) is the
// grid path: act on, and update, `selectedImageIds`.
async function deleteSelected(idsOverride = null) {
  const scoped = Array.isArray(idsOverride) && idsOverride.length > 0;
  const baseIds = scoped ? idsOverride : selectedImageIds.value;
  if (!baseIds.length) return;
  const isScrapheapSelection = isScrapheapView.value;

  // For non-scrapheap deletions, expand collapsed stacks to all their members
  // while only deleting the selected pictures from expanded stacks.
  let idsToRemove;
  // Set when an expanded stack's leader is deleted: the backend promotes the
  // next live member to leader, but our base fetch (lastFetchedGridImages)
  // holds only leaders, so we refetch to bring the promoted leader/members back.
  let deletedExpandedStackLeader = false;
  if (!isScrapheapSelection) {
    const imageById = new Map(
      (allGridImages.value || [])
        .filter((img) => img && img.id != null)
        .map((img) => [String(img.id), img]),
    );

    const resolved = new Set();
    const collapsedStackIds = new Set();

    for (const id of baseIds) {
      const img = imageById.get(String(id));
      const stackId = getPictureStackId(img);
      if (!stackId || expandedStackIds.value.has(stackId)) {
        // No stack, or stack is expanded: delete only this picture.
        resolved.add(id);
        if (stackId && getStackPositionValue(img) === 0) {
          // Deleting the leader of an expanded stack: a refetch is needed so the
          // backend-promoted new leader (absent from the leaders-only base list)
          // and the stack's remaining members reappear.
          deletedExpandedStackLeader = true;
        }
      } else {
        // Collapsed stack: delete all members.
        collapsedStackIds.add(stackId);
      }
    }

    for (const stackId of collapsedStackIds) {
      try {
        const stack = await getStack(stackId);
        const memberIds = stack?.picture_ids;
        if (Array.isArray(memberIds) && memberIds.length) {
          for (const mid of memberIds) resolved.add(mid);
        }
      } catch (e) {
        console.error(
          "Failed to fetch stack members for delete, falling back to selected ids:",
          e,
        );
        // Fallback: delete the originally-targeted picture(s) from this stack.
        for (const id of baseIds) {
          const img = imageById.get(String(id));
          if (getPictureStackId(img) === stackId) resolved.add(id);
        }
      }
    }

    idsToRemove = [...resolved];
  } else {
    idsToRemove = baseIds.slice();
  }

  if (isScrapheapSelection) {
    // Destructive & irreversible: route through the tokenized Delete-forever
    // confirm, which names any reference-folder ORIGINALS being destroyed. The
    // scrapheap purge runs on @confirm (runScrapheapSelectionPurge).
    openDeleteForeverForSelection(idsToRemove, scoped);
    return;
  }

  // Pictures frozen by a locked set are SKIPPED by the bulk delete - the server
  // returns 200 with `skipped_locked`. If every id in the batch is one of those,
  // the request provably cannot delete anything, so don't send it: explain
  // instead. (Attempting and silently no-op'ing is what produced the reported
  // bug.) The client-side lock map is the same source the grid already gates its
  // context menu on; the server stays authoritative for every mixed batch below.
  const lockedInBatch = idsToRemove.filter((id) =>
    lockedSetsStore.isLocked(id),
  );
  if (idsToRemove.length && lockedInBatch.length === idsToRemove.length) {
    showLockedDeleteNotice(
      buildLockedDeleteMessage({
        lockedCount: lockedInBatch.length,
        deletedCount: 0,
      }),
    );
    return;
  }

  try {
    // Soft-delete via the bulk endpoint instead of one DELETE per id, which
    // floods the browser/Electron per-host connection pool on large selections
    // (excess sockets get reset and surface as axios "Network Error"). Chunk to
    // the server's per-request id cap so a huge selection is a handful of
    // requests, not thousands; each chunk broadcasts a single ``removed`` event.
    const BULK_DELETE_CHUNK = 1000;
    // Ids the server refused because a locked set freezes them. Collected across
    // every chunk so the outcome message counts the whole operation.
    const skippedLocked = new Set();
    for (let i = 0; i < idsToRemove.length; i += BULK_DELETE_CHUNK) {
      const chunk = idsToRemove.slice(i, i + BULK_DELETE_CHUNK);
      const resp = await deletePictures(chunk);
      const skipped = resp?.skipped_locked;
      if (Array.isArray(skipped)) {
        for (const id of skipped) skippedLocked.add(String(id));
      }
    }

    // Only drop the tiles the server actually deleted. Removing the skipped ones
    // too would hide pictures that are still in the library until the next
    // refetch - a second, quieter version of the same lie.
    const removedIds = idsToRemove.filter(
      (id) => !skippedLocked.has(String(id)),
    );
    // Keep the tiles mounted and greyed while the undo is still one click away,
    // and let the receipt's own clock decide when the grid closes the gap.
    // `markGhosted` declines when there is no undo window to hold them open for
    // (a read-only session), in which case they go immediately, as they always
    // did. The own-origin `removed` echo of this same delete is suppressed by
    // the realtime decision table, so nothing else races to drop them.
    if (!operationStore.markGhosted(removedIds)) {
      removeImagesById(removedIds);
    }
    // Overlay path: don't touch the grid selection. removeImagesById already
    // drops any deleted id from it (a no-op when the overlay picture wasn't
    // grid-selected), so the user's grid selection is left exactly as it was.
    if (!scoped) {
      // Keep the frozen pictures selected: they are still there, and the user's
      // next move (after unlocking the set) is to delete exactly them.
      selectedImageIds.value = selectedImageIds.value.filter((id) =>
        skippedLocked.has(String(id)),
      );
      // Drop the range-selection anchor unless it is one of the survivors -
      // otherwise a later shift-click would anchor on a deleted picture.
      const stillSelected = new Set(selectedImageIds.value.map(String));
      if (!stillSelected.has(String(lastSelectedImageId.value))) {
        lastSelectedImageId.value = null;
      }
    }
    if (deletedExpandedStackLeader) {
      // Repopulate so the promoted stack leader and remaining members reappear
      // (same pattern as create/dissolve/remove-from-stack).
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
    }
    emit("refresh-sidebar");
    showLockedDeleteNotice(
      buildLockedDeleteMessage({
        lockedCount: skippedLocked.size,
        deletedCount: removedIds.length,
      }),
    );
  } catch (err) {
    console.error("Bulk delete failed", err);
    noticeStore.error(`Couldn't delete those pictures. ${errorDetail(err)}`, {
      key: "pictures-delete",
    });
  }
}

// ── Keep cover only ─────────────────────────────────────────────────────────
// One preview over one selection, then one call. The dialog is the only consent
// (no type-to-confirm: this is a recoverable soft delete, not the destruction of
// an on-disk original). See docs/design/keep-cover-only.md.

/** The dotted op type the backend records, so the receipt note can match it. */
const KEEP_COVER_ONLY_OP_TYPE = "stack.keep_cover_only";

const keepCoverOnlyOpen = ref(false);
const keepCoverOnlyPreview = ref(null);
const keepCoverOnlyLoading = ref(false);
const keepCoverOnlyPreviewFailed = ref(false);
const keepCoverOnlyBusy = ref(false);
/**
 * The stacks the open dialog is describing, frozen when it opened.
 *
 * The confirm must act on exactly what the preview reported. Reading the live
 * selection again at confirm time would let a background refetch, or a stray
 * click behind the scrim, move the target out from under the figures the user
 * agreed to.
 */
const keepCoverOnlyTargetStackIds = ref([]);
// Guards a preview that lands after its dialog was closed or reopened.
let keepCoverOnlyRunToken = 0;

async function openKeepCoverOnly() {
  if (isReadOnly.value || keepCoverOnlyBusy.value) return;
  const stackIds = keepCoverOnlyStacks.value
    .map((stack) => Number(stack.id))
    .filter((id) => Number.isFinite(id));
  if (!stackIds.length) return;
  keepCoverOnlyTargetStackIds.value = stackIds;
  keepCoverOnlyPreview.value = null;
  keepCoverOnlyPreviewFailed.value = false;
  keepCoverOnlyLoading.value = true;
  keepCoverOnlyOpen.value = true;
  const token = ++keepCoverOnlyRunToken;
  try {
    const report = await previewKeepCoverOnly({
      stackIds,
    });
    if (token !== keepCoverOnlyRunToken) return;
    keepCoverOnlyPreview.value = report;
  } catch (err) {
    if (token !== keepCoverOnlyRunToken) return;
    // A failed preview is its own state in the dialog, never a screen of
    // zeroes: "nobody could ask" and "there is nothing to collapse" must not
    // look the same.
    console.error(
      `Keep cover only: the preview for stacks [${stackIds.join(", ")}] could not be read`,
      err,
    );
    keepCoverOnlyPreviewFailed.value = true;
  } finally {
    if (token === keepCoverOnlyRunToken) keepCoverOnlyLoading.value = false;
  }
}

function closeKeepCoverOnly() {
  // Bumping the token orphans any preview still in flight, so it cannot write
  // figures into a dialog the user has already dismissed.
  keepCoverOnlyRunToken += 1;
  keepCoverOnlyOpen.value = false;
  keepCoverOnlyPreview.value = null;
  keepCoverOnlyLoading.value = false;
  keepCoverOnlyPreviewFailed.value = false;
  keepCoverOnlyTargetStackIds.value = [];
}

async function runKeepCoverOnly() {
  const stackIds = keepCoverOnlyTargetStackIds.value;
  if (!stackIds.length || keepCoverOnlyBusy.value) return;
  keepCoverOnlyBusy.value = true;
  try {
    const result = await keepCoverOnly({
      stackIds,
    });
    const movedIds = Array.isArray(result?.picture_ids_moved)
      ? result.picture_ids_moved
      : [];
    // Same treatment as the grid's own delete: the tiles stay ghosted in place
    // while undo is one click away, and the receipt's clock decides when the
    // grid closes the gap. `markGhosted` declines in a read-only session, where
    // there is no undo window at all.
    if (movedIds.length && !operationStore.markGhosted(movedIds)) {
      removeImagesById(movedIds);
    }
    selectedImageIds.value = [];
    lastSelectedImageId.value = null;
    // What the run deliberately left alone rides the SAME pill as what it did,
    // as a second sentence, rather than a notice competing with it.
    operationStore.noteNextReceipt(
      KEEP_COVER_ONLY_OP_TYPE,
      keepCoverOnlySkipNote(result),
    );
    // Raises "Kept the cover of N stacks · M pictures to the Scrapheap · Undo".
    operationStore.refresh();
    emit("refresh-sidebar");
    // NO grid refetch here, deliberately. The surviving covers are no longer
    // stacks and their badges have to go, but `debouncedFetchAllGridImages()`
    // would rebuild the grid without the scrapheaped copies and take the
    // ghosted tiles off the screen, and with them the one-click undo they
    // advertise. The badge is reconciled instead by the server's own
    // `fields: ["stack_count"]` announcement over the covers, which reaches
    // THIS tab as well as any other and routes to `refreshStackFacets`
    // (useGridRealtimeSync's stack-facet branch). One mechanism, so the undo
    // and a second tab converge through exactly the same path.
    keepCoverOnlyOpen.value = false;
    keepCoverOnlyPreview.value = null;
    keepCoverOnlyTargetStackIds.value = [];
  } catch (err) {
    console.error(
      `Keep cover only failed for stacks [${stackIds.join(", ")}]`,
      err,
    );
    noticeStore.error(`Couldn't collapse those stacks. ${errorDetail(err)}`, {
      key: "keep-cover-only",
    });
  } finally {
    keepCoverOnlyBusy.value = false;
  }
}

// ── Rotate in place ─────────────────────────────────────────────────────────
// Applied on click, with no dialog and no confirmation: the step is instant,
// lossless and reversible, so the safety net is the receipt's Undo rather than
// a question asked before every quarter-turn.
//
// Gestures are SERIALISED rather than refused while one is in flight, for the
// same reason as in the lightbox: two rotates the same way are a legitimate
// 180°, and each request reads a picture's current orientation and writes the
// next one, so two in flight over the same picture lose a turn between them.
let rotateQueue = Promise.resolve();

/**
 * Re-read these cards' thumbnail URLs from the server.
 *
 * The card's `?v=` token is the server's, and it moves when the bitmap does -
 * but only if the client actually asks for it again. This is the AWAITED way to
 * ask, for a named set of cards, which is what a rotate needs: it has to settle
 * before the receipt narrates the turn.
 *
 * `fetchThumbnailsBatch` now also applies the server's URL over its own
 * `?v=<imported_at>` pre-fill, so `refreshGridImage`'s trailing batch would
 * eventually repair these tiles too - but it fires that batch un-awaited, and
 * "eventually" is not something a caller can sequence against.
 *
 * Taking the server's URL verbatim - never stamping a buster here - is what
 * keeps the token a server contract rather than a mirror of one. Its shape
 * (`?v=<W>x<H>` plus an `o<orientation>` suffix once a picture has been turned)
 * is deliberately not parsed here for the same reason.
 *
 * @param {Array<number|string>} pictureIds
 * @returns {Promise<void>}
 */
async function refreshThumbnailUrls(pictureIds) {
  const ids = [
    ...new Set(
      (Array.isArray(pictureIds) ? pictureIds : [])
        .map((id) => getPictureId(id))
        .filter((id) => id !== null),
    ),
  ];
  if (!ids.length) return;
  let thumbData;
  try {
    thumbData = await getThumbnails(ids);
  } catch (e) {
    console.error(
      `refreshThumbnailUrls: could not re-read the thumbnails of pictures ` +
        `[${ids.join(", ")}]; their tiles keep the bitmap they last painted`,
      e,
    );
    return;
  }
  const next = allGridImages.value.slice();
  let changed = false;
  for (let i = 0; i < next.length; i++) {
    const img = next[i];
    if (!img || img.id == null) continue;
    const record = thumbData?.[getPictureId(img.id)];
    const url = record?.thumbnail;
    if (!url) continue;
    const absolute = appendShareToken(
      url.startsWith("http") ? url : `${props.backendUrl}${url}`,
    );
    if (absolute === img.thumbnail) continue;
    // The server's answer is taken VERBATIM, null included. A rotate NULLs the
    // stored dimensions to re-queue the bitmap, and those are the dimensions of
    // the bitmap that is now sideways: keeping them (the old `|| img.…`
    // fallback) held the tile in its pre-rotate shape until the regeneration
    // sweep landed, and then it jumped. Absent, `displayedAspectRatio` falls
    // through to the raw dimensions and turns them by the orientation, which is
    // the shape the regenerated bitmap will have.
    const width = Number(record.thumbnail_width);
    const height = Number(record.thumbnail_height);
    next[i] = {
      ...img,
      thumbnail: absolute,
      thumbnail_width: width > 0 ? width : null,
      thumbnail_height: height > 0 ? height : null,
    };
    changed = true;
  }
  if (changed) allGridImages.value = next;
}

// Pictures whose rotate is in flight, and which way they are turning. Drives the
// tile's in-flight overlay; the icon names the DIRECTION rather than showing a
// generic spinner, because the gesture is unconfirmed and instant-looking and
// the one thing a user needs echoed back is which way they just asked for.
const rotatingDirectionById = ref(new Map());

function markRotating(pictureIds, direction) {
  for (const id of Array.isArray(pictureIds) ? pictureIds : []) {
    const key = getPictureId(id);
    if (key !== null) rotatingDirectionById.value.set(key, direction);
  }
}

function clearRotating(pictureIds) {
  for (const id of Array.isArray(pictureIds) ? pictureIds : []) {
    const key = getPictureId(id);
    if (key !== null) rotatingDirectionById.value.delete(key);
  }
}

/** The in-flight rotate icon for this card, or `null` when it is not turning. */
function rotatingIconFor(img) {
  const direction = rotatingDirectionById.value.get(getPictureId(img?.id));
  if (direction === ROTATE_CW) return "mdi-file-rotate-right";
  if (direction === ROTATE_CCW) return "mdi-file-rotate-left";
  return null;
}

// How long a tile will wait for its new bitmap before landing anyway. The commit
// is deliberately blocked on the decode (see `applyRotatedCards`), so a request
// that never answers would otherwise leave the card mid-gesture for good.
// ponytail: a flat ceiling, not a per-picture budget - one number is enough
// until a batch of very large thumbnails proves otherwise.
const ROTATE_BITMAP_WAIT_MS = 5000;

/**
 * Fetch and decode a bitmap off-screen, so the next paint that uses it is free.
 *
 * Never rejects and never blocks for long: a URL that 404s or hangs resolves
 * anyway, because the caller is holding a visible commit on this promise.
 *
 * @param {string} url
 * @returns {Promise<void>}
 */
function preloadBitmap(url) {
  if (!url) return Promise.resolve();
  return new Promise((resolve) => {
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(done, ROTATE_BITMAP_WAIT_MS);
    const probe = new Image();
    probe.onload = () => {
      // decode() as well as load(), so the commit is not followed by a decode
      // on the main thread - that is visible as a hitch on a large thumbnail.
      const decoded =
        typeof probe.decode === "function"
          ? probe.decode().catch(() => {})
          : Promise.resolve();
      decoded.then(done, done);
    };
    probe.onerror = done;
    probe.src = url;
  });
}

/**
 * Land a rotate on its cards as ONE visual change.
 *
 * A turned picture changes two things about its tile - the shape of the box the
 * justified layout packs, and the bitmap inside it - and they arrive from
 * different places: the orientation from `/pictures/{id}/metadata`, the URL and
 * its cache token from `POST /pictures/thumbnails` (the metadata endpoint
 * carries no thumbnail URL at all). Applying each as it arrives is what made a
 * rotate happen twice on screen: the cell flipped to portrait first, stretching
 * the old landscape bitmap into it, and only then did the new bitmap arrive and
 * the picture turn.
 *
 * So both reads happen first, the new bitmap is fetched AND decoded off-screen,
 * and only then is one write made to `allGridImages` - after which the shape and
 * the pixels change in the same frame. The visible cost is that the tile does
 * nothing for a moment, which is what the in-flight overlay is for.
 *
 * FIELDS ONLY: nothing is inserted, removed or reordered. A turned photo does
 * not move in the grid, which is why this can be safe inside an open overlay
 * where a refetch would not be.
 *
 * @param {Array<number|string>} pictureIds - pictures the server actually turned.
 * @returns {Promise<void>} settles once the cards show the new picture.
 */
async function applyRotatedCards(pictureIds) {
  const ids = [
    ...new Set(
      (Array.isArray(pictureIds) ? pictureIds : [])
        .map((id) => getPictureId(id))
        .filter((id) => id !== null),
    ),
  ];
  if (!ids.length) return;

  let thumbData;
  let records;
  try {
    [thumbData, records] = await Promise.all([
      getThumbnails(ids),
      Promise.all(ids.map((id) => fetchImageInfo(id, { force: true }))),
    ]);
  } catch (e) {
    console.error(
      `applyRotatedCards: could not re-read pictures [${ids.join(", ")}]; ` +
        "their tiles keep the picture they last painted",
      e,
    );
    return;
  }

  // The patch each card will take, built before anything is written so the
  // write itself is one assignment.
  const patchById = new Map();
  ids.forEach((id, index) => {
    const record = thumbData?.[id];
    const info = records[index];
    const patch = {};
    if (info && !Array.isArray(info)) Object.assign(patch, info);
    if (record?.thumbnail) {
      patch.thumbnail = appendShareToken(
        record.thumbnail.startsWith("http")
          ? record.thumbnail
          : `${props.backendUrl}${record.thumbnail}`,
      );
    }
    if (record) {
      // Taken verbatim, null included: a rotate NULLs the stored dimensions to
      // re-queue the bitmap, and `displayedAspectRatio` then falls through to
      // the raw dimensions turned by the orientation - which is the shape the
      // regenerated bitmap will have, so the tile does not move again later.
      const width = Number(record.thumbnail_width);
      const height = Number(record.thumbnail_height);
      patch.thumbnail_width = width > 0 ? width : null;
      patch.thumbnail_height = height > 0 ? height : null;
      // The boxes live in a coordinate space the turn just redefined.
      patch.faces = Array.isArray(record.faces) ? record.faces : [];
      patch.detections = Array.isArray(record.detections)
        ? record.detections
        : [];
    }
    patchById.set(id, patch);
  });

  await Promise.all(
    [...patchById.values()].map((patch) => preloadBitmap(patch.thumbnail)),
  );

  const next = allGridImages.value.slice();
  let changed = false;
  for (let i = 0; i < next.length; i++) {
    const img = next[i];
    if (!img || img.id == null) continue;
    const patch = patchById.get(getPictureId(img.id));
    if (!patch) continue;
    next[i] = { ...img, ...patch, idx: img.idx ?? i };
    // The range is re-read on the next visit, so a later background sweep's
    // regenerated bitmap is picked up rather than masked by this write.
    invalidateThumbnailIndex(i);
    changed = true;
  }
  if (changed) allGridImages.value = next;
}

/**
 * One 90° step over a set of pictures, plus the refresh it owes.
 *
 * Mixed selections are the normal case and are handled by DOING the work that
 * can be done: the server splits its answer into rotated / unsupported /
 * skipped, and everything it left alone rides the receipt as a second sentence
 * rather than a notice of its own.
 *
 * @param {Array<number|string>} pictureIds - captured at gesture time.
 * @param {string} direction - `"cw"` or `"ccw"`.
 * @returns {Promise<void>}
 */
async function runRotate(pictureIds, direction) {
  markRotating(pictureIds, direction);
  try {
    const result = await rotatePictures(pictureIds, direction);
    // The note is armed immediately before the refresh that narrates the
    // action, so the two arrive together.
    operationStore.noteNextReceipt(ROTATE_OP_TYPE, rotateSkipNote(result));
    operationStore.refresh();
    const rotated = Array.isArray(result?.rotated_picture_ids)
      ? result.rotated_picture_ids
      : [];
    if (!rotated.length) return;
    await applyRotatedCards(rotated);
  } catch (e) {
    console.error(
      `Rotate ${direction} failed for pictures [${pictureIds.join(", ")}]`,
      e,
    );
    noticeStore.error(`Couldn't rotate those pictures. ${errorDetail(e)}`, {
      key: "rotate-pictures",
    });
  } finally {
    clearRotating(pictureIds);
  }
}

/**
 * Take one rotate gesture over the selection, behind anything already running.
 *
 * @param {string} direction - `"cw"` or `"ccw"`.
 * @returns {Promise<void>} settles when THIS step has landed.
 */
function rotateSelectedPictures(direction) {
  // Snapshotted now, not when the turn comes up: the selection is the one the
  // user was looking at when they asked.
  const pictureIds = (
    Array.isArray(selectedImageIds.value) ? selectedImageIds.value : []
  ).slice();
  if (!pictureIds.length) return rotateQueue;
  rotateQueue = rotateQueue.then(() => runRotate(pictureIds, direction));
  return rotateQueue;
}

async function handleSetProjectForSelected(payload) {
  if (partialStackGroupingReason.value) {
    noticeStore.warning(partialStackGroupingReason.value, {
      key: "partial-stack-grouping",
    });
    return;
  }
  const explicitPictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
    : [];
  const basePictureIds = explicitPictureIds.length
    ? explicitPictureIds
    : selectedImageIds.value;
  const expandStacks = payload?.expandStacks !== false;
  const pictureIds = await resolveProjectSelectionPictureIds(
    basePictureIds,
    expandStacks,
  );
  if (!pictureIds.length) {
    return;
  }

  const nextProjectIdRaw = payload?.projectId ?? null;
  const nextProjectId =
    nextProjectIdRaw === null || nextProjectIdRaw === undefined
      ? null
      : Number(nextProjectIdRaw);
  if (nextProjectId !== null && !Number.isFinite(nextProjectId)) {
    noticeStore.error("That project is no longer available.", {
      key: "project-invalid",
    });
    return;
  }

  const action = String(payload?.action || "set").toLowerCase();

  try {
    if (action === "removed") {
      if (nextProjectId === null) {
        return;
      }
      await setPicturesProject(pictureIds, nextProjectId, {
        mode: "remove",
      });
    } else if (action === "added") {
      await setPicturesProject(pictureIds, nextProjectId, {
        mode: "add",
      });
    } else {
      await setPicturesProject(pictureIds, nextProjectId);
    }

    // Project membership only scopes the grid query in the project view:
    // useGridFetch appends `project_id` exclusively when projectViewMode is
    // "project" (_appendSelectionParams). In the global view (All Pictures and
    // every other non-project-scoped view) an assignment changes neither which
    // pictures match the query nor their sort position, and the card itself
    // renders no project data, so a refetch would only flicker the grid and
    // throw away scroll position and selection. Refetch only where membership
    // can actually move a picture into or out of the view. The sibling set path
    // (handleOverlayAddedToSet) and the sidebar drag-drop path
    // (App.handleImagesMoved) already scope their grid work the same way.
    if (isProjectScopedView.value) {
      if (overlayOpen.value) {
        // A project change while the overlay is open would replace
        // allGridImages, breaking the filmstrip. Defer the refetch until the
        // overlay closes.
        pendingOverlayGridRefresh.value = true;
      } else {
        // Only arm the scroll-preserving flag when a fetch actually follows;
        // otherwise it leaks into the next unrelated fetch.
        preserveScrollOnNextFetch.value = true;
        await fetchAllGridImages({ force: true });
        updateVisibleThumbnails();
      }
    }
    emit("refresh-sidebar");
  } catch (err) {
    const message = errorDetail(err) || err?.message || String(err);
    noticeStore.error(`Couldn't update the project. ${message}`, {
      key: "project-update",
    });
  }
}

async function resolveProjectSelectionPictureIds(
  pictureIdsInput,
  expandStacks = true,
) {
  const selectedIds = (Array.isArray(pictureIdsInput) ? pictureIdsInput : [])
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  if (!selectedIds.length) {
    return [];
  }

  const resolved = new Set(selectedIds);
  const fetchedById = new Map(
    (Array.isArray(lastFetchedGridImages.value)
      ? lastFetchedGridImages.value
      : []
    )
      .filter((img) => img && img.id != null)
      .map((img) => [String(img.id), img]),
  );

  const stacksToExpand = new Map();
  if (!expandStacks) {
    return Array.from(resolved).sort((a, b) => a - b);
  }

  for (const pictureId of selectedIds) {
    const fetchedImg = fetchedById.get(String(pictureId));
    if (!fetchedImg) {
      continue;
    }
    const stackId = getPictureStackId(fetchedImg);
    const stackCount = getStackBadgeCount(fetchedImg);
    if (!stackId || !Number.isFinite(stackCount) || stackCount <= 1) {
      continue;
    }
    if (!stacksToExpand.has(stackId)) {
      stacksToExpand.set(stackId, stackCount);
    }
  }

  for (const [stackId, stackCount] of stacksToExpand.entries()) {
    await ensureStackMembersLoaded(stackId, stackCount);
    const loaded = expandedStackMembers.value.get(stackId);
    const loadedImages = Array.isArray(loaded?.images) ? loaded.images : [];
    const fallbackImages = getLocalStackMembers(stackId);
    const source = loadedImages.length ? loadedImages : fallbackImages;
    for (const img of source) {
      const id = Number(img?.id);
      if (Number.isFinite(id) && id > 0) {
        resolved.add(id);
      }
    }
  }

  return Array.from(resolved).sort((a, b) => a - b);
}

// ============================================================
// SELECTION BAR + SCRAPHEAP
// ============================================================
const isScrapheapView = computed(() => {
  const scrapheapId = String(
    SCRAPHEAP_PICTURES_ID || "SCRAPHEAP",
  ).toUpperCase();
  const selected = String(selectionStore.selectedCharacter || "").toUpperCase();
  return selected === scrapheapId;
});

// ── Scrapheap auto-empty policy ─────────────────────────────────────────────
// Two separate things, deliberately not conflated:
//   * the retention WINDOW - a server setting, shared via the store, shown in
//     the scrapheap header so the policy is visible where the actions are;
//   * each picture's purge DATE - `purge_at`, stamped by the server, which
//     already accounts for the grace period applied when the window is
//     shortened. The grid only formats the distance to it and never re-derives
//     a purge date from the window.
// An exempt picture carries `auto_purge_exempt: true`, a null `purge_at`, and
// an `auto_purge_exempt_reason` naming WHY - "protected" (a reference-folder
// original) or "locked" (frozen by a locked set). The two get different badges
// because only the second is something the user can change (unlock the set).
// The descriptor itself is built by `buildPurgeBadge` in utils/retention.js so
// the three states are unit-testable without a grid.
const scrapheapRetentionLabel = computed(() =>
  isScrapheapView.value && scrapheapRetentionStore.loaded
    ? scrapheapRetentionStore.label
    : "",
);

// Captured on entry to the scrapheap view so every tile counts down from one
// instant and no two labels can disagree within a render.
const purgeNowMs = ref(Date.now());

watch(
  isScrapheapView,
  (isScrapheap) => {
    if (!isScrapheap) return;
    purgeNowMs.value = Date.now();
    scrapheapRetentionStore.fetchRetention();
  },
  { immediate: true },
);

/**
 * Auto-purge badge state for the pictures in the current render window, keyed
 * by picture id. Built once per window rather than per cell so a large virtual
 * grid does not re-derive it for every tile on every render.
 * @returns {Map<number|string, {kind: string, icon: string, label: string, title: string}>}
 */
const scrapheapPurgeBadges = computed(() => {
  const badges = new Map();
  if (!isScrapheapView.value) return badges;
  // Nothing is on a clock when auto-empty is off, so neither a countdown nor a
  // "won't auto-delete" badge means anything - the header already states the
  // policy. Also suppressed until the policy is known, so the badges can't
  // appear and then vanish once the fetch lands.
  if (!scrapheapRetentionStore.loaded || scrapheapRetentionStore.isNever) {
    return badges;
  }
  const now = purgeNowMs.value;
  const dateFormat =
    typeof userPrefsStore.dateFormat === "string"
      ? userPrefsStore.dateFormat
      : "locale";
  const formatDate = (iso) => formatUserDate(iso, dateFormat);
  for (const img of gridImagesToRender.value || []) {
    if (!img || img.id == null) continue;
    const badge = buildPurgeBadge(img, { now, formatDate });
    if (badge) badges.set(img.id, badge);
  }
  return badges;
});

/**
 * Badge descriptor for one tile, or `undefined` outside the scrapheap view.
 * @param {Object} img - grid picture.
 */
function getScrapheapPurgeBadge(img) {
  return img && img.id != null
    ? scrapheapPurgeBadges.value.get(img.id)
    : undefined;
}

const selectedMediaSupport = computed(() => {
  const ids = Array.isArray(selectedImageIds.value)
    ? selectedImageIds.value
    : [];
  if (!ids.length) {
    return { hasImages: false, hasVideos: false };
  }

  const images = Array.isArray(allGridImages.value) ? allGridImages.value : [];
  const imageById = new Map(
    images
      .filter((img) => img && img.id != null)
      .map((img) => [String(img.id), img]),
  );

  let hasImages = false;
  let hasVideos = false;
  for (const id of ids) {
    const img = imageById.get(String(id));
    if (!img) {
      hasImages = true;
      continue;
    }
    if (isVideo(img)) {
      hasVideos = true;
    } else {
      hasImages = true;
    }
  }

  return { hasImages, hasVideos };
});

// The selection's picture records, in selection order, for the gates that need
// to look at the files rather than only count them.
function selectedPictureRecords() {
  const ids = Array.isArray(selectedImageIds.value) ? selectedImageIds.value : [];
  if (!ids.length) return [];
  const byId = new Map(
    (Array.isArray(allGridImages.value) ? allGridImages.value : [])
      .filter((img) => img && img.id != null)
      .map((img) => [String(img.id), img]),
  );
  return ids.map((id) => byId.get(String(id))).filter(Boolean);
}

// Why the context menu's rotate items are greyed, or null while at least one
// selected picture can be rotated in place.
const selectionRotateBlockReason = computed(() =>
  buildRotateBlockReason(selectedPictureRecords()),
);

const scrapheapEmptying = ref(false);
const showSelectionBar = computed(() => {
  return selectedImageIds.value.length > 0 || selectedFaceIds.value.length > 0;
});

// ── The grid action pill's search half ──────────────────────────────────────
const searchResultsActive = computed(
  () =>
    Boolean(searchStore.searchQuery && searchStore.searchQuery.length > 0) ||
    reverseImageSearchPictureIds.value.length > 0 ||
    Boolean(faceLikenessSearchFaceId.value) ||
    Boolean(faceSearchCharacter.value),
);

/**
 * The status sentence, split into the numeral and the rest so the pill can give
 * the count its own weight without regex-splitting a string that may contain
 * the user's query.
 *
 * The scope is folded into the sentence rather than standing beside it as a
 * separate "Searched X only" note, and the QUERY is named: nothing else on
 * screen says what was searched once the toolbar popover closes, which made
 * recall the only way back to it.
 */
const searchStatus = computed(() => {
  const total = allGridImages.value.length;

  if (faceSearchCharacter.value) {
    // Just "N matches". The person is named twice over already, on the Assign
    // button and by the sidebar row the search was armed from, and this label
    // sat in front of the two sliders and a bulk-write button in a pill that
    // has to fit them all without wrapping.
    const n = faceSearchMatches.value.length;
    return { count: n, label: n === 1 ? "match" : "matches" };
  }
  if (faceLikenessSearchFaceId.value) {
    return { count: total, label: "similar faces" };
  }
  if (reverseImageSearchPictureIds.value.length > 1) {
    return {
      count: total,
      label: `matches for ${reverseImageSearchPictureIds.value.length} pictures`,
    };
  }
  if (reverseImageSearchPictureIds.value.length) {
    return { count: total, label: "matches for this picture" };
  }

  const query = (searchStore.searchQuery || "").trim();
  const scope = selectionStore.isAllPicturesActive
    ? ""
    : ` in ${props.activeCategoryLabel}`;
  const forQuery = query ? ` for "${query}"` : "";
  if (total === 0) {
    return { count: null, label: `No matches${forQuery}${scope}` };
  }
  return { count: total, label: `matches${forQuery}${scope}` };
});

/**
 * Focus rescue. GridActionPill raises this when the half holding focus is about
 * to unmount and there is no sibling half to take it: without somewhere to go,
 * focus falls to <body> and a keyboard user drops out of the tab order (WCAG
 * 2.4.3). The scroll wrapper is the grid's own focus home.
 */
function restoreGridFocus() {
  const el = scrollWrapper.value;
  if (!el) return;
  if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
  el.focus({ preventScroll: true });
}
// Grouping (project/set/character) membership is stack-atomic: it can only be
// changed for a WHOLE stack at once - a collapsed stack tile (which represents
// the whole stack) or every member of an expanded stack selected. Changing a
// strict subset of a stack's members is refused; the user must unstack first.
const PARTIAL_STACK_GROUPING_MESSAGE =
  "Unstack first to change the project, set or character of individual stack pictures.";

const partialStackGroupingReason = computed(() => {
  const ids = (selectedImageIds.value || [])
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  if (!ids.length) return null;
  const idSet = new Set(ids);
  const images = allGridImages.value || [];
  const byId = new Map(
    images
      .filter((img) => img && img.id != null)
      .map((img) => [String(img.id), img]),
  );

  // Which stacks does the selection touch? (Members share stack_id even when
  // expanded - only the collapsed leader tile carries a >1 stack_count, so we
  // must group by stack_id, not by per-tile count.)
  const stackIds = new Set();
  for (const id of ids) {
    const img = byId.get(String(id));
    if (!img) continue;
    const stackId = getPictureStackId(img);
    if (stackId) stackIds.add(stackId);
  }

  for (const stackId of stackIds) {
    // A collapsed stack tile represents the whole stack → allowed.
    if (!expandedStackIds.value.has(stackId)) continue;
    // An expanded stack is whole-stack only when every rendered member is
    // selected.
    const memberIds = images
      .filter(
        (img) => img && img.id != null && getPictureStackId(img) === stackId,
      )
      .map((img) => Number(img.id));
    const allSelected =
      memberIds.length > 0 && memberIds.every((mid) => idSet.has(mid));
    if (!allSelected) return PARTIAL_STACK_GROUPING_MESSAGE;
  }
  return null;
});

// Lock reason for the current selection: non-null when at least one selected
// picture is frozen by a locked set. The label-data mutations in the context
// menu (tag, auto-tag, description, delete) would 423 for the whole batch, so we
// disable them and surface this as the tooltip. Naming the count + set(s) tells
// the user exactly why and how to unlock. A single locked picture is enough.
const selectionLockReason = computed(() => {
  const ids = (selectedImageIds.value || [])
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  if (!ids.length) return null;
  const lockedIds = ids.filter((id) => lockedSetsStore.isLocked(id));
  if (!lockedIds.length) return null;
  const names = new Set();
  for (const id of lockedIds) {
    for (const name of lockedSetsStore.lockedSetNames(id)) names.add(name);
  }
  const joined = [...names].join(", ");
  const noun = lockedIds.length === 1 ? "picture is" : "pictures are";
  return (
    `Locked - ${lockedIds.length} selected ${noun} in the locked set ` +
    `'${joined}'. Unlock the set to edit: right-click it in the sidebar and ` +
    `choose Unlock.`
  );
});

// ── Keep cover only ─────────────────────────────────────────────────────────
// The action's unit is the STACK: a selection names stacks, each collapses to
// its own cover, and loose pictures in a mixed selection are ignored. That is
// only honest because the menu label counts stacks, which is what these two
// computeds feed. See docs/design/keep-cover-only.md.

// Both computations are pure and live in `utils/keepCoverOnly.js`, where they
// are unit-tested without a mounted grid; the whole-stack locked refusal in
// particular is a rule that has to hold, not a rule that has to render.
const keepCoverOnlyStacks = computed(() =>
  selectedKeepCoverOnlyStacks({
    selectedIds: selectedImageIds.value,
    images: allGridImages.value,
  }),
);

const keepCoverOnlyStackCount = computed(
  () => keepCoverOnlyStacks.value.length,
);

const keepCoverOnlyLockReason = computed(() =>
  buildKeepCoverOnlyLockReason({
    stacks: keepCoverOnlyStacks.value,
    isLocked: (id) => lockedSetsStore.isLocked(id),
    lockedSetNames: (id) => lockedSetsStore.lockedSetNames(id),
  }),
);

const selectedExpandedCount = computed(() => {
  const selectedSet = new Set(
    selectedImageIds.value
      .map((id) => Number(id))
      .filter((id) => Number.isFinite(id) && id > 0),
  );
  const visibleIds = new Set(
    allGridImages.value
      .map((img) => Number(img?.id))
      .filter((id) => Number.isFinite(id) && id > 0),
  );
  const isFullVisibleSelection =
    visibleIds.size > 0 &&
    selectedSet.size === visibleIds.size &&
    [...visibleIds].every((id) => selectedSet.has(id));

  const isTopCategorySelection = [
    String(ALL_PICTURES_ID),
    String(UNASSIGNED_PICTURES_ID),
  ].includes(String(selectionStore.selectedCharacter ?? ""));

  const isSetCategorySelection =
    selectionStore.selectedSet !== null &&
    selectionStore.selectedSet !== undefined &&
    String(selectionStore.selectedSet) !== "";

  const supportsAuthoritativeCategoryCount =
    isTopCategorySelection || isSetCategorySelection;

  const hasNoAdditionalFilters =
    !(searchStore.searchQuery || "").trim() &&
    filterStore.mediaTypeFilter === "all" &&
    (filterStore.comfyuiModelFilter || []).length === 0 &&
    (filterStore.comfyuiLoraFilter || []).length === 0 &&
    filterStore.minScoreFilter == null &&
    filterStore.maxScoreFilter == null &&
    !filterStore.unscoredOnlyFilter &&
    filterStore.smartScoreBucketFilter == null &&
    filterStore.resolutionBucketFilter == null;

  // Keep the info count aligned with sidebar summary for full category selections.
  if (
    isFullVisibleSelection &&
    supportsAuthoritativeCategoryCount &&
    hasNoAdditionalFilters &&
    totalCurrentCategoryCount.value > 0
  ) {
    return totalCurrentCategoryCount.value;
  }

  const seenStacks = new Set();
  let total = 0;
  for (const img of allGridImages.value) {
    if (!img || !img.id) continue;
    if (!selectedSet.has(Number(img.id))) continue;
    const stackId = getPictureStackId(img);
    const stackCount = Number(img.stack_count ?? img.stackCount ?? 0);
    if (stackId != null && stackCount > 1) {
      if (seenStacks.has(stackId)) continue;
      seenStacks.add(stackId);
      total += stackCount;
      continue;
    }
    total += 1;
  }
  return total;
});
const showScrapheapBar = computed(() => {
  return isScrapheapView.value;
});
const SCRAPHEAP_BAR_HEIGHT_PX = 30;
const wrapperStyle = { position: "relative", height: "100%" };
const scrollWrapperStyle = computed(() => ({
  position: "absolute",
  top: showScrapheapBar.value
    ? `calc(var(--selbar-height, 48px) + ${SCRAPHEAP_BAR_HEIGHT_PX}px)`
    : "var(--selbar-height, 48px)",
  left: "0",
  right: "0",
  bottom: "0",
}));
const scrapheapEmptyDisabled = computed(() => {
  return (
    scrapheapEmptying.value ||
    imagesLoading.value ||
    filteredGridCount.value === 0
  );
});
const scrapheapRestoring = ref(false);
const scrapheapRestoreDisabled = computed(() => {
  return (
    scrapheapRestoring.value ||
    imagesLoading.value ||
    filteredGridCount.value === 0
  );
});

// After a permanent purge the backend reports which snapshots still hold the
// deleted pictures' metadata (the archives are not scrubbed). Surface that so
// the user can delete those snapshots if the deletion was for privacy.
const snapshotsWithDeleted = ref([]);
const snapshotsWithDeletedOpen = ref(false);

// ── Delete-forever confirm (Scrapheap DAM 1.1) ────────────────────────────────
// The tokenized destructive confirm that replaces the native window.confirm on
// the two scrapheap purge paths. Its counts and the exact on-disk paths of the
// protected reference-folder ORIGINALS come from an AUTHORITATIVE server preview
// (POST /pictures/scrapheap/delete-preview) - NOT from the virtualized grid /
// grid_lite payload, which could omit protected originals outside the loaded
// window and undercount. When protected originals are present the dialog runs a
// three-way, type-to-confirm flow (delete all incl. protected / delete
// unprotected only / cancel). Data-safety critical: if the preview can't be
// loaded we fail SAFE and never open the destructive confirm.
const deleteForeverOpen = ref(false);
const deleteForeverBusy = ref(false); // DELETE request in flight
const deleteForeverLoading = ref(false); // preview request in flight
const deleteForeverTotalCount = ref(0);
const deleteForeverProtectedCount = ref(0);
const deleteForeverUnprotectedCount = ref(0);
const deleteForeverProtectedPaths = ref([]);
// Pictures a locked set freezes. Destroyed by NEITHER delete-forever action, so
// the dialog states it up front. Defensive: a server that has not shipped
// `locked_count` yet yields 0, i.e. the pre-existing copy, unchanged.
const deleteForeverLockedCount = ref(0);
const deleteForeverMode = ref("selection"); // "selection" | "all"
const deleteForeverIds = ref([]);
// True when the pending purge was scoped to an explicit id list by the overlay
// context menu, so runScrapheapSelectionPurge must not mutate the grid selection.
const deleteForeverScoped = ref(false);
// Single-use confirmation minted by the delete preview for exactly this
// selection. The server refuses the purge without it, so it is cleared on every
// new preview and after every attempt - a stale one must never be replayed.
const deleteForeverConfirmToken = ref("");

function openDeleteForeverForSelection(ids, scoped = false) {
  deleteForeverMode.value = "selection";
  deleteForeverScoped.value = scoped;
  deleteForeverIds.value = ids.slice();
  loadDeletePreview(ids.slice());
}

function openDeleteForeverForAll() {
  deleteForeverMode.value = "all";
  deleteForeverScoped.value = false;
  deleteForeverIds.value = [];
  loadDeletePreview(null);
}

/**
 * Fetch the authoritative deletion preview (counts + the FULL protected-original
 * path list) and open the confirm. `ids` = selected ids, or null for empty-all.
 * Fails SAFE: on any error the destructive confirm is NOT opened.
 */
async function loadDeletePreview(ids) {
  if (deleteForeverLoading.value) return;
  deleteForeverLoading.value = true;
  deleteForeverConfirmToken.value = "";
  try {
    const d =
      (await previewScrapheapDelete(ids ?? null)) ?? {};
    deleteForeverConfirmToken.value = String(d.confirm_token ?? "");
    deleteForeverTotalCount.value = Number(d.total_count) || 0;
    deleteForeverProtectedCount.value = Number(d.protected_count) || 0;
    deleteForeverUnprotectedCount.value = Number(d.unprotected_count) || 0;
    deleteForeverProtectedPaths.value = Array.isArray(d.protected)
      ? d.protected.map((p) => p?.file_path).filter(Boolean)
      : [];
    deleteForeverLockedCount.value = Number(d.locked_count) || 0;
    if (deleteForeverTotalCount.value === 0) {
      // Nothing to delete (e.g. the scrapheap was emptied concurrently).
      return;
    }
    if (
      deleteForeverLockedCount.value > 0 &&
      deleteForeverProtectedCount.value === 0 &&
      deleteForeverUnprotectedCount.value === 0
    ) {
      // Nothing in either destroyable bucket: every targeted picture is frozen
      // by a lock, so both actions would be no-ops. Read straight off the
      // server's disjoint classification rather than inferring it from
      // `total_count`, so the UI can't disagree with the sweep about what
      // survives. Explain instead of opening a destructive confirm.
      showLockedDeleteNotice(
        buildLockedDeleteMessage({
          lockedCount: deleteForeverLockedCount.value,
          deletedCount: 0,
        }),
      );
      return;
    }
    deleteForeverOpen.value = true;
  } catch (err) {
    console.error("Failed to load scrapheap delete preview", err);
    // Fail SAFE: the destructive confirm is never opened on an unverified
    // basis, and the user is told why rather than seeing nothing happen.
    noticeStore.error(
      "Couldn't check which files would be destroyed, so nothing was deleted.",
      { key: "scrapheap-delete-preview" },
    );
  } finally {
    deleteForeverLoading.value = false;
  }
}

function cancelDeleteForever() {
  deleteForeverConfirmToken.value = "";
  deleteForeverOpen.value = false;
}

// payload: { includeProtected: boolean } from DeleteForeverDialog.
async function confirmDeleteForever(payload) {
  const includeProtected = Boolean(payload?.includeProtected);
  if (deleteForeverMode.value === "all") {
    await runEmptyScrapheap(includeProtected);
  } else {
    await runScrapheapSelectionPurge(deleteForeverIds.value, includeProtected);
  }
}

async function runScrapheapSelectionPurge(idsToRemove, includeProtected) {
  if (!idsToRemove || !idsToRemove.length) {
    deleteForeverOpen.value = false;
    return;
  }
  deleteForeverBusy.value = true;
  try {
    const resp = await purgeScrapheap({
      pictureIds: idsToRemove,
      includeProtected,
      confirmToken: deleteForeverConfirmToken.value,
    });
    // Locked pictures survive a purge too, and this endpoint reports them under
    // the same `skipped_locked` name as the bulk soft-delete. Same rule as
    // there: never drop a tile the server kept.
    const skippedLocked = new Set(
      (Array.isArray(resp?.data?.skipped_locked)
        ? resp.data.skipped_locked
        : []
      ).map(String),
    );
    if (includeProtected) {
      removeImagesById(
        idsToRemove.filter((id) => !skippedLocked.has(String(id))),
      );
    } else {
      // The protected originals in the selection are intentionally kept - refetch
      // so the grid reflects exactly what the server removed.
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
    }
    // Grid path only: keep the frozen pictures selected so the user can retry
    // after unlocking. The overlay-scoped path leaves the grid selection alone.
    if (!deleteForeverScoped.value) {
      selectedImageIds.value = selectedImageIds.value.filter((id) =>
        skippedLocked.has(String(id)),
      );
      const stillSelected = new Set(selectedImageIds.value.map(String));
      if (!stillSelected.has(String(lastSelectedImageId.value))) {
        lastSelectedImageId.value = null;
      }
    }
    updateVisibleThumbnails();
    emit("refresh-sidebar");
    showSnapshotsWithDeleted(resp);
    showLockedDeleteNotice(
      buildLockedDeleteMessage({
        lockedCount: skippedLocked.size,
        // Counted from the server's own report, not from what we asked for.
        deletedCount: idsToRemove.length - skippedLocked.size,
      }),
    );
  } catch (err) {
    console.error("Scrapheap purge failed", err);
    noticeStore.error(`Couldn't delete those pictures. ${errorDetail(err)}`, {
      key: "scrapheap-purge",
    });
  } finally {
    // The server spends the confirmation on the first attempt, so a retry must
    // go back through the preview rather than replaying a dead token.
    deleteForeverConfirmToken.value = "";
    deleteForeverBusy.value = false;
    deleteForeverOpen.value = false;
  }
}

function showSnapshotsWithDeleted(response) {
  if (userPrefsStore.hidePurgeSnapshotWarning) return;
  const snaps = response?.data?.snapshots_with_deleted;
  if (Array.isArray(snaps) && snaps.length) {
    snapshotsWithDeleted.value = snaps;
    snapshotsWithDeletedOpen.value = true;
  }
}

// Only re-entrancy blocks this. It must NOT be gated on `scrapheapEmptyDisabled`:
// that computed exists for the placeholder button's `:disabled` state, and its
// `imagesLoading || filteredGridCount === 0` terms describe the loaded grid, not
// the heap. The sidebar context menu navigates here and asks for the confirm in
// the same gesture, so it always arrives while the view-switch fetch is still in
// flight and the grid has just been reset - the request was silently dropped and
// the menu item looked like it only navigated. What is actually in the heap is
// decided one step later by the AUTHORITATIVE server preview, which counts the
// whole heap (the grid can undercount) and declines to open the dialog when
// there is nothing to delete.
function confirmEmptyScrapheap() {
  if (scrapheapEmptying.value) return;
  // Route through the tokenized Delete-forever confirm (names any reference-folder
  // originals being destroyed); the purge runs on @confirm.
  openDeleteForeverForAll();
}

async function runEmptyScrapheap(includeProtected) {
  // Re-entrancy only. The user has typed the confirm and we hold a single-use
  // server token for exactly this purge; the grid's load state must not veto it,
  // or a refetch that lands while the dialog is open would swallow a confirmed
  // deletion. The server re-validates the token and the scope.
  if (scrapheapEmptying.value) return;
  scrapheapEmptying.value = true;
  deleteForeverBusy.value = true;
  try {
    const resp = await purgeScrapheap({
      includeProtected,
      confirmToken: deleteForeverConfirmToken.value,
    });
    // Clear + refetch reconciles either case: when only the unprotected subset
    // was purged, the refetch brings the kept protected originals back.
    allGridImages.value = [];
    selectedImageIds.value = [];
    selectedFaceIds.value = [];
    lastSelectedImageId.value = null;
    updateVisibleThumbnails();
    emit("refresh-sidebar");
    fetchAllGridImages().then(() => {
      updateVisibleThumbnails();
    });
    showSnapshotsWithDeleted(resp);
  } catch (e) {
    console.error("Failed to empty the scrapheap", e);
    noticeStore.error(`Couldn't empty the scrapheap. ${errorDetail(e)}`, {
      key: "scrapheap-empty",
    });
  } finally {
    // Spent server-side on the first attempt; a retry re-runs the preview.
    deleteForeverConfirmToken.value = "";
    scrapheapEmptying.value = false;
    deleteForeverBusy.value = false;
    deleteForeverOpen.value = false;
  }
}

async function confirmRestoreScrapheap() {
  if (scrapheapRestoreDisabled.value) return;
  const confirmed = confirm(
    "Restore all scrapheap pictures? This will make them visible again.",
  );
  if (!confirmed) return;
  scrapheapRestoring.value = true;
  try {
    await restoreScrapheap(undefined);
    allGridImages.value = [];
    selectedImageIds.value = [];
    selectedFaceIds.value = [];
    lastSelectedImageId.value = null;
    updateVisibleThumbnails();
    emit("refresh-sidebar");
    fetchAllGridImages().then(() => {
      updateVisibleThumbnails();
    });
  } catch (e) {
    console.error("Failed to restore the scrapheap", e);
    noticeStore.error(`Couldn't restore the scrapheap. ${errorDetail(e)}`, {
      key: "scrapheap-restore-all",
    });
  } finally {
    scrapheapRestoring.value = false;
  }
}

async function openReferenceLocation(picId) {
  try {
    await openPictureLocation(picId);
  } catch (err) {
    // Was a silent ignore ("the OS might not support it"), which left a click
    // doing nothing with no explanation. The cause is worth stating: it is
    // usually a headless/remote server with no desktop file manager.
    console.warn(`Failed to open the location of picture ${picId}`, err);
    noticeStore.warning(
      "Couldn't open that folder - the server has no desktop file manager.",
      { key: "open-location" },
    );
  }
}

// ============================================================
// IMPORT
// ============================================================
const imageImporterRef = ref(null);
// Handle images-uploaded event from ImageImporter
async function handleImagesUploaded(payload) {
  pauseGridAutoUpdates.value = false;
  pendingGridRefreshAfterImport.value = false;
  emit("import-ended");
  const results = Array.isArray(payload?.results) ? payload.results : [];
  const pictureIds = Array.from(
    new Set(
      results
        .map((entry) => entry?.picture_id)
        .filter((id) => id !== null && id !== undefined),
    ),
  );
  if (pictureIds.length) {
    try {
      const selectedSetId = selectionStore.selectedSet;
      const selectedCharacterId = selectionStore.selectedCharacter;
      const selectedCharacterKey = String(selectedCharacterId ?? "");
      const skipCharacter = [
        String(ALL_PICTURES_ID),
        String(UNASSIGNED_PICTURES_ID),
        String(SCRAPHEAP_PICTURES_ID),
      ].includes(selectedCharacterKey);
      if (selectedSetId != null && selectedSetId !== "") {
        await Promise.all(
          pictureIds.map((id) =>
            addPictureToSet(selectedSetId, id),
          ),
        );
      } else if (!skipCharacter && selectedCharacterId != null) {
        await addCharacterFaces(selectedCharacterId, pictureIds);
      }
    } catch (e) {
      console.error("Failed to associate imported pictures:", e);
    }
  }
  resetThumbnailState();
  allGridImages.value = [];
  selectedImageIds.value = [];
  lastSelectedImageId.value = null;
  fetchAllGridImages({ force: true }).then(() => {
    updateVisibleThumbnails();
  });
  emit("refresh-sidebar");
}

function handleImportStarted() {
  pauseGridAutoUpdates.value = true;
  pendingGridRefreshAfterImport.value = false;
  emit("import-started");
}

function runDeferredGridRefreshAfterImport() {
  if (!pendingGridRefreshAfterImport.value) {
    return;
  }
  pendingGridRefreshAfterImport.value = false;
  preserveScrollOnNextFetch.value = true;
  debouncedFetchAllGridImages({ force: true });
  fetchAllPicturesCount();
}

function handleImportCancelled() {
  pauseGridAutoUpdates.value = false;
  emit("import-ended");
  runDeferredGridRefreshAfterImport();
}

function handleImportErrored() {
  pauseGridAutoUpdates.value = false;
  emit("import-ended");
  runDeferredGridRefreshAfterImport();
}

// Lazy dispatch object resolved after useGridFetch + useStackOrdering are called.
// useGridFetch is called before useStackOrdering (so it can return
// debouncedFetchAllGridImages for use by useStackOrdering), but it needs
// stack callbacks that only exist after useStackOrdering returns.  These
// _stackOps wrappers are filled in immediately after useStackOrdering.
const _stackOps = {
  collapseStackImages: null,
  mapGridImages: null,
  syncExpandAllStacksFromFetchedImages: null,
  refreshExpandedStacksAfterFetch: null,
};
const lastGridVersionRefreshAt = ref(Date.now());
const WS_TAG_FULL_REFRESH_MIN_INTERVAL_MS = 6000;
const lastWsTagFullRefreshAt = ref(0);
let wsTagFullRefreshTimer = null;

function scheduleWsTagFullRefresh() {
  const now = Date.now();
  const elapsed = now - lastWsTagFullRefreshAt.value;
  if (elapsed >= WS_TAG_FULL_REFRESH_MIN_INTERVAL_MS) {
    lastWsTagFullRefreshAt.value = now;
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages({ force: true });
    return;
  }
  if (wsTagFullRefreshTimer) {
    return;
  }
  const waitMs = WS_TAG_FULL_REFRESH_MIN_INTERVAL_MS - elapsed + 25;
  wsTagFullRefreshTimer = setTimeout(
    () => {
      wsTagFullRefreshTimer = null;
      lastWsTagFullRefreshAt.value = Date.now();
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages({ force: true });
    },
    Math.max(25, waitMs),
  );
}

watch(
  () => gridStore.gridVersion,
  () => {
    if (pauseGridAutoUpdates.value) {
      pendingGridRefreshAfterImport.value = true;
      return;
    }
    const now = Date.now();
    if (now - lastGridVersionRefreshAt.value < 1200) {
      return;
    }
    lastGridVersionRefreshAt.value = now;
    if (skipNextWsRefresh.value) {
      skipNextWsRefresh.value = false;
      return;
    }
    if (overlayOpen.value) {
      // Overlay is open - quietly refresh in the background without clearing
      // allGridImages, so the overlay doesn't lose focus or state mid-edit.
      debouncedFetchAllGridImages();
      fetchAllPicturesCount();
      return;
    }
    gridReady.value = false;
    emptyStateDelayPassed.value = false;
    if (preserveScrollOnNextFetch.value && scrollWrapper.value) {
      pendingScrollTop.value = scrollWrapper.value.scrollTop;
    } else {
      pendingScrollTop.value = null;
    }
    resetThumbnailState();
    if (!preserveScrollOnNextFetch.value) {
      allGridImages.value = [];
      selectedImageIds.value = [];
      lastSelectedImageId.value = null;
    }
    // Force the refetch to bypass the 1200ms de-dup cache: if the grid was
    // just cleared we must not skip the fetch, otherwise the grid stays blank.
    debouncedFetchAllGridImages({ force: true });
    if (preserveScrollOnNextFetch.value) {
      preserveScrollOnNextFetch.value = false;
    }
    fetchAllPicturesCount();
  },
);

// ============================================================
// SELECTION STATE + TOUCH SELECTION MODE
// (moved to useMultiSelect composable)
// ============================================================
const {
  selectedImageIds,
  lastSelectedImageId,
  cursorIdx,
  isImageSelected,
  touchSelectMode,
  suppressTouchClickId,
  lastPointerWasTouch,
  handleTouchStart,
  handleTouchMove,
  handleTouchEnd,
  exitTouchSelectMode,
  selectedFaceIds,
  isFaceSelected,
  toggleFaceSelection,
  clearFaceSelection,
  onFaceBboxDragStart,
  clearSelection,
} = useMultiSelect();

// The locked-delete cards (`showLockedDeleteNotice`) are scoped to the context
// they describe: the sentence is about THIS selection in THIS view, and it
// carries an action, so it is sticky and nothing would otherwise take it down.
// The signature is everything the message asserts - which pictures are selected,
// where they are being viewed, and which sets are locked. Change any of them and
// the card is describing the past, so it goes. Unlocking the set is the
// important one: it is the fix the card asks for, and leaving the warning up
// afterwards would make the fix look like it failed.
//
// Declared here rather than beside `showLockedDeleteNotice` because
// `useScopedNotice` evaluates the signature immediately to seed its watcher, so
// `selectedImageIds` has to exist first.
const lockedDeleteNotice = useScopedNotice(
  ["delete-skipped-locked", "delete-skipped-locked-help"],
  () =>
    [
      // Joined in place, not sorted: the getter re-runs on every selection
      // change, and sorting a 10k-picture selection per click to catch a pure
      // reorder is not worth it. A reorder is a user action anyway, so treating
      // it as a context change is the right answer, not a false positive.
      selectedImageIds.value.join(","),
      String(selectionStore.selectedCharacter ?? ""),
      String(selectionStore.selectedSet ?? ""),
      String(projectStore.selectedProjectId ?? ""),
      // Locked sets are few, so a per-set membership fingerprint is cheap and
      // catches an unlock, a re-lock and a membership edit alike.
      lockedSetsStore.sets
        .map((s) => `${s?.id}:${(s?.picture_ids || []).length}`)
        .sort()
        .join(","),
    ].join("|"),
);

// ============================================================
// VIEWPORT + RENDER
// ============================================================
// VIEWPORT + RENDER
// ============================================================
const allGridImagesLength = computed(() => allGridImages.value?.length ?? 0);

// Aspect ratio for every grid item (present for ALL images from the base grid
// listing, not just fetched thumbnails). Missing/zero dimensions (unimported
// pictures, unprobed videos) fall back to square so justified packing never
// divides by zero.
const gridAspectRatios = computed(() =>
  (allGridImages.value || []).map(displayedAspectRatio),
);

const {
  initialRender,
  divisibleViewWindow,
  renderBuffer,
  visibleStart,
  visibleEnd,
  rowHeight,
  renderStart,
  renderEnd,
  topSpacerHeight,
  bottomSpacerHeight,
  updateRowHeightFromGrid,
  recalculateVisibleRange,
  onGridScroll,
  scrollCursorIntoView,
  isJustifiedMode,
  justifiedLayout,
} = useVirtualScroll(scrollWrapper, gridContainer, props, allGridImagesLength, {
  onVisibleRangeChange: () => updateVisibleThumbnails(),
  afterRowHeightUpdate: () => refreshAllThumbnailInfoDisplays(),
  getAspectRatios: () => gridAspectRatios.value,
});

// ---- Justified-mode card geometry ----
// Each card carries an exact inline width/height from the packed layout so the
// flex-wrap lines break exactly where useJustifiedLayout computed the rows -
// the invariant the spacer/fetch arithmetic depends on.
const justifiedInfoRowExtra = computed(() =>
  gridStore.compactMode ? 0 : THUMBNAIL_INFO_ROW_HEIGHT,
);

// A collapsed stack's deck edges (StackEdgeTicks) peek OUTSIDE the cover, up and
// to the right in `--space-1` steps, so they need room around the tile. Only the
// square grid has it: 4px of `.thumbnail-card` padding plus the 4px grid gap.
// Justified packs the cover to its exact box behind a 2px seam, and compact
// removes both the padding and the gap. In either, the peek lands on the
// NEIGHBOURING photo instead of on the canvas. The permanent stack count badge
// rides the same guard as the ticks, so those modes still declare the stack.
const stackDeckEdgesFit = computed(
  () => !isJustifiedMode.value && !gridStore.compactMode,
);

function gridImageLayoutIndex(img, localIdx) {
  return Number.isFinite(img?.idx) ? img.idx : renderStart.value + localIdx;
}

function _justifiedItemGeometry(img, localIdx) {
  if (!isJustifiedMode.value) return null;
  const layout = justifiedLayout.value;
  if (!layout || !layout.rowHeights.length) return null;
  const globalIdx = gridImageLayoutIndex(img, localIdx);
  if (globalIdx < 0 || globalIdx >= layout.itemScaledWidths.length) return null;
  const row = rowOfIndex(layout.rowStarts, globalIdx);
  return {
    width: layout.itemScaledWidths[globalIdx],
    cardHeight: layout.rowHeights[row],
  };
}

function getJustifiedCardStyle(img, localIdx) {
  const geo = _justifiedItemGeometry(img, localIdx);
  if (!geo) return null;
  return {
    width: `${geo.width}px`,
    height: `${geo.cardHeight}px`,
    flex: "0 0 auto",
  };
}

function getJustifiedThumbStyle(img, localIdx) {
  const geo = _justifiedItemGeometry(img, localIdx);
  if (!geo) return null;
  // The thumbnail box is the card minus the (non-scaling) info row.
  return { height: `${geo.cardHeight - justifiedInfoRowExtra.value}px` };
}

// Container style for the two layout modes. Square keeps the original CSS
// grid; justified switches to flex-wrap (via the class) with the packing gap
// bound inline so JS arithmetic and CSS can never drift.
const gridContainerStyle = computed(() => {
  const base = { position: "relative", ...badgeCssVars.value };
  if (isJustifiedMode.value) {
    return {
      ...base,
      columnGap: `${JUSTIFIED_ROW_GAP}px`,
      rowGap: `${JUSTIFIED_ROW_GAP}px`,
    };
  }
  return {
    ...base,
    gridTemplateColumns: `repeat(${gridStore.columns}, minmax(0, 1fr))`,
  };
});

// ============================================================
// OVERLAY STATE
// ============================================================
const overlayOpen = ref(false);
// The lightbox is a deliberately-dark surface, so the notice host switches to
// its `--on-dark` variant while it is up (notice-surface.md §2.5). The host
// lives in App.vue (it must also render where there is no grid), so the state
// has to travel up; `isOverlayOpen()` on the imperative API is a getter, not a
// reactive signal, and cannot drive a binding.
watch(overlayOpen, (isOpen) => emit("update:overlay-open", isOpen));
const overlayImageId = ref(null);

// ---- Overlay route tracking ----
const _overlayRoute = useRoute();
const _overlayRouter = useRouter();
// Prevents the route watcher from re-triggering when we push the route ourselves.
let _overlayRoutePushPending = false;

function _pushOverlayRoute(id) {
  _overlayRoutePushPending = true;
  const query = { ..._overlayRoute.query, overlay: String(id) };
  _overlayRouter.replace({ query }).finally(() => {
    _overlayRoutePushPending = false;
  });
}

function _removeOverlayRoute() {
  _overlayRoutePushPending = true;
  const { overlay: _removed, ...rest } = _overlayRoute.query;
  _overlayRouter.replace({ query: rest }).finally(() => {
    _overlayRoutePushPending = false;
  });
}

watch(
  () => _overlayRoute.query.overlay,
  (id) => {
    if (_overlayRoutePushPending) return;
    if (id) {
      if (!overlayOpen.value || String(overlayImageId.value) !== String(id)) {
        openOverlay({ id });
      }
    } else {
      if (overlayOpen.value) {
        closeOverlay();
      }
    }
  },
);
const overlayInitialExpandedStackIds = ref([]);
const overlayGuestScore = computed(() => {
  const id = overlayImageId.value;
  if (id == null) return 0;
  // guestScoreMap holds optimistic updates; fall back to img.score which the
  // backend now returns pre-overridden with the guest score for READ sessions.
  const fromMap = guestScoreMap.value.get(Number(id));
  if (fromMap != null) return fromMap;
  const img = allGridImages.value?.find((i) => i.id === Number(id));
  return img?.score ?? 0;
});
// Set to true when a tag mutation was deferred (applyTagFilter=true, overlay
// open). Triggers a filtered grid refetch once the overlay closes.
const pendingTagFilterRefresh = ref(false);
// Set to true when a grid-mutating operation (set removal, stack change,
// smart-score re-rank) was deferred to avoid the filmstrip losing its current
// picture while the overlay is open. Triggers a full refetch on close.
const pendingOverlayGridRefresh = ref(false);
// Pictures whose smart score changed while the overlay was open. Reconciled by
// repositioning each card on close; see handleOverlayChange. Above this many the
// per-id fetches cost more than one re-sort, so we fall back to a full reload -
// mirroring MAX_TARGETED_UPDATE in useGridRealtimeSync.
const pendingOverlaySmartScoreIds = new Set();
const MAX_DEFERRED_SMART_SCORE_REPOSITIONS = 25;
// When fetchAllGridImages completes while the overlay is open, the resulting
// image list is stored here instead of being written to allGridImages directly.
// Applied to allGridImages when the overlay closes.
const pendingGridImages = ref(null);

// ============================================================
// GRID FETCH TELEMETRY
// ============================================================
const GRID_FETCH_TELEMETRY_MAX_ENTRIES = 400;
const gridFetchTelemetryByLoadId = new Map();

function getGridFetchTelemetryStore() {
  if (typeof window === "undefined") return null;
  if (!Array.isArray(window.__PIXLSTASH_GRID_FETCH_TELEMETRY__)) {
    window.__PIXLSTASH_GRID_FETCH_TELEMETRY__ = [];
  }
  return window.__PIXLSTASH_GRID_FETCH_TELEMETRY__;
}

function trimGridFetchTelemetryStore(store) {
  while (store.length > GRID_FETCH_TELEMETRY_MAX_ENTRIES) {
    store.shift();
  }
}

function getGridFetchContextSummary(fetchKey) {
  if (!fetchKey || typeof fetchKey !== "string") {
    return {};
  }
  try {
    const parsed = JSON.parse(fetchKey);
    return {
      selectedSort: parsed?.selectedSort ?? null,
      selectedCharacter: parsed?.selectedCharacter ?? null,
      selectedSet: parsed?.selectedSet ?? null,
      searchQuery: parsed?.searchQuery ?? "",
      mediaTypeFilter: parsed?.mediaTypeFilter ?? "all",
    };
  } catch {
    return {};
  }
}

function onGridFetchStart(payload) {
  const loadId = Number(payload?.loadId) || 0;
  const startedAtMs = getNowMs();
  const context = getGridFetchContextSummary(payload?.fetchKey);
  const record = {
    loadId,
    startedAtMs,
    endedAtMs: null,
    elapsedMs: null,
    visibleMetadataMs: null,
    firstBatchCount: null,
    total: null,
    fetchMode: null,
    force: payload?.force === true,
    success: null,
    resultCount: null,
    visibleStart: payload?.visibleStart ?? null,
    visibleEnd: payload?.visibleEnd ?? null,
    ...context,
  };
  gridFetchTelemetryByLoadId.set(loadId, record);
  const store = getGridFetchTelemetryStore();
  if (store) {
    store.push(record);
    trimGridFetchTelemetryStore(store);
  }
}

function onGridVisibleMetadataReady(payload) {
  const loadId = Number(payload?.loadId) || 0;
  const record = gridFetchTelemetryByLoadId.get(loadId);
  if (!record) return;
  record.visibleMetadataMs = Math.max(0, getNowMs() - record.startedAtMs);
  record.firstBatchCount = Number(payload?.firstBatchCount) || 0;
  if (Number.isFinite(Number(payload?.total))) {
    record.total = Number(payload.total);
  }
}

function onGridFetchDone(payload) {
  const loadId = Number(payload?.loadId) || 0;
  const record = gridFetchTelemetryByLoadId.get(loadId);
  if (record) {
    Object.assign(record, payload || {}, {
      endedAtMs: getNowMs(),
      fetchMode: payload?.fetchMode ?? null,
      success: payload?.success === true,
      elapsedMs: Number(payload?.elapsedMs) || 0,
      resultCount: Number(payload?.resultCount) || 0,
    });
    gridFetchTelemetryByLoadId.delete(loadId);
  }
  console.debug("[GridFetchTelemetry]", {
    loadId,
    fetchMode: payload?.fetchMode ?? null,
    success: payload?.success === true,
    elapsedMs: Number(payload?.elapsedMs) || 0,
    resultCount: Number(payload?.resultCount) || 0,
    countMs: payload?.countMs ?? null,
    placeholderMs: payload?.placeholderMs ?? null,
    firstBatchMs: payload?.firstBatchMs ?? null,
    tailBatchMs: payload?.tailBatchMs ?? null,
    backgroundTotalMs: payload?.backgroundTotalMs ?? 0,
    backgroundNetworkTotalMs: payload?.backgroundNetworkTotalMs ?? 0,
    backgroundUiTotalMs: payload?.backgroundUiTotalMs ?? 0,
    backgroundSlowestBatchMs: payload?.backgroundSlowestBatchMs ?? 0,
    backgroundSlowestNetworkBatchMs:
      payload?.backgroundSlowestNetworkBatchMs ?? 0,
    backgroundSlowestUiBatchMs: payload?.backgroundSlowestUiBatchMs ?? 0,
    backgroundBatchCount: payload?.backgroundBatchCount ?? 0,
    postProcessMs: payload?.postProcessMs ?? null,
  });
}

if (
  typeof window !== "undefined" &&
  typeof window.__PIXLSTASH_DUMP_GRID_FETCH_TELEMETRY__ !== "function"
) {
  window.__PIXLSTASH_DUMP_GRID_FETCH_TELEMETRY__ = (limit = 40) => {
    const parsedLimit = Math.max(1, Number(limit) || 40);
    const rows = (window.__PIXLSTASH_GRID_FETCH_TELEMETRY__ || [])
      .slice(-parsedLimit)
      .map((row) => ({
        loadId: row.loadId,
        mode: row.fetchMode,
        success: row.success,
        elapsedMs: row.elapsedMs,
        countMs: row.countMs,
        placeholderMs: row.placeholderMs,
        firstBatchMs: row.firstBatchMs,
        tailBatchMs: row.tailBatchMs,
        backgroundTotalMs: row.backgroundTotalMs,
        backgroundNetworkTotalMs: row.backgroundNetworkTotalMs,
        backgroundUiTotalMs: row.backgroundUiTotalMs,
        backgroundSlowestBatchMs: row.backgroundSlowestBatchMs,
        backgroundSlowestNetworkBatchMs: row.backgroundSlowestNetworkBatchMs,
        backgroundSlowestUiBatchMs: row.backgroundSlowestUiBatchMs,
        backgroundBatchCount: row.backgroundBatchCount,
        postProcessMs: row.postProcessMs,
        visibleMetadataMs: row.visibleMetadataMs,
        firstBatchCount: row.firstBatchCount,
        total: row.total,
        resultCount: row.resultCount,
        selectedSort: row.selectedSort,
        searchQuery: row.searchQuery,
      }));
    console.table(rows);
    return rows;
  };
}

// ============================================================
// DRAG & DROP STATE + SOURCE HELPERS
// (moved to useGridDragDrop composable)
// ============================================================
const {
  dragOverlayVisible,
  dragOverlayMessage,
  isDragSourceImage,
  stackReorderDrag,
  stackReorderHoverId,
  stackReorderHoverSide,
  setStackReorderHoverId,
  setStackReorderHoverSide,
  isStackReorderTarget,
  isStackReorderTargetSide,
  prepareThumbnailNativeDrag,
  handleThumbnailPointerRelease,
  handleGridDragEnter,
  handleGridDragOver,
  handleGridDragLeave,
  clearGridDragOverlay,
  handleGridDrop,
  handleThumbnailNativeDragStart,
  handleDragEnd,
  handleContainerDragStart,
} = useGridDragDrop(
  {
    selectedImageIds,
    touchSelectMode,
    imageImporterRef,
    thumbnailRefs,
    dragPreviewRefs,
    prefetchFullImage,
    reviewOverlayOpen,
    isImageGhosted,
  },
  props,
);

// ============================================================
// GRID FETCH STATE + FETCH FUNCTIONS
// (moved to useGridFetch composable)
// useGridFetch is called before useStackOrdering so it can return
// debouncedFetchAllGridImages for useStackOrdering to use.
// Stack callbacks are lazy-dispatched via _stackOps (wired below).
// ============================================================
const {
  imagesLoading,
  totalAllPicturesCount,
  totalAllPicturesCountLoaded,
  totalCurrentCategoryCount,
  gridReady,
  lastFetchError,
  lastFetchSuccess,
  smartScoreLoadingVisible,
  buildGridFetchKey,
  buildPictureIdsQueryParams,
  fetchAllGridImages,
  fetchAllPicturesCount,
  debouncedFetchAllGridImages,
} = useGridFetch(
  {
    allGridImages,
    lastFetchedGridImages,
    scrollWrapper,
    preserveScrollOnNextFetch,
    pendingScrollTop,
    overlayOpen,
    pendingGridImages,
    pendingOverlayGridRefresh,
    visibleStart,
    visibleEnd,
    divisibleViewWindow,
    initialRender,
    rowHeight,
    sharedPictureIds,
    guestConsentState,
    guestSessionId,
    highlightNextFetch,
    hasLoadedOnce,
    previousImageIds,
    normalizedSelectedCharacterIds,
    normalizedSelectedSetIds,
    hasSetSelection,
    isSetOverlapView,
    isMultiCharacterView,
    primarySelectedSetId,
    smartScoreProgress,
    exportProgress,
    reverseImageSearchPictureIds,
    faceLikenessSearchFaceId,
    faceSearchCharacter,
    faceSearchThreshold,
    faceSearchMinRefs,
    faceSearchRanked,
  },
  props,
  {
    collapseStackImages: (images) => _stackOps.collapseStackImages(images),
    mapGridImages: (images) => _stackOps.mapGridImages(images),
    syncExpandAllStacksFromFetchedImages: () =>
      _stackOps.syncExpandAllStacksFromFetchedImages(),
    refreshExpandedStacksAfterFetch: () =>
      _stackOps.refreshExpandedStacksAfterFetch(),
    resetThumbnailState,
    triggerNewImageHighlight,
    updateVisibleThumbnails,
    fetchThumbnailsBatch,
    maybeRefreshOverlayForComfyui,
    startSmartScoreProgress,
    completeSmartScoreProgress,
    onGridFetchStart,
    onGridVisibleMetadataReady,
    onGridFetchDone,
  },
);

// ── Character membership undo/redo must reconcile a character grid ───
// The history endpoint changes the face metadata and the operation store updates
// its history/receipt, but the resulting pictures_changed event carries this
// tab's own origin and is deliberately suppressed by the realtime grid sync.
// That leaves a character view showing pictures whose assignment was just
// undone or redone until an unrelated full refresh. Subscribe to the completed
// shared history action, as DuplicateQueue does for its domain state, and
// re-read only when character membership can affect the active grid.
const CHARACTER_MEMBERSHIP_HISTORY_ACTIONS = new Set([
  "undo",
  "undoTo",
  "undoBatchById",
  "redo",
]);
const CHARACTER_MEMBERSHIP_OP_TYPES = new Set([
  "characters.assign",
  "characters.unassign",
]);

const isCharacterScopedView = computed(() => {
  if (normalizedSelectedCharacterIds.value.length > 0) return true;
  const selected = selectionStore.selectedCharacter;
  if (selected == null) return false;
  const key = String(selected).toUpperCase();
  return ![
    String(ALL_PICTURES_ID).toUpperCase(),
    String(UNASSIGNED_PICTURES_ID).toUpperCase(),
    String(SCRAPHEAP_PICTURES_ID).toUpperCase(),
  ].includes(key);
});

/** Operations a history action is about to touch, before its stack moves. */
function characterMembershipHistoryTargets(name, args) {
  if (name === "undo") {
    return operationStore.nextUndo ? [operationStore.nextUndo] : [];
  }
  if (name === "undoTo") {
    const past = operationStore.past ?? [];
    const index = past.findIndex((op) => op?.id === args?.[0]);
    return index < 0 ? [] : past.slice(0, index + 1);
  }
  if (name === "undoBatchById") {
    return (operationStore.operations ?? []).filter(
      (op) => op?.batch_id === args?.[0],
    );
  }
  if (name === "redo") {
    return operationStore.nextRedo ? [operationStore.nextRedo] : [];
  }
  return [];
}

operationStore.$onAction(({ name, args, after }) => {
  if (!CHARACTER_MEMBERSHIP_HISTORY_ACTIONS.has(name)) return;
  const targets = characterMembershipHistoryTargets(name, args);
  if (
    !targets.some((op) =>
      CHARACTER_MEMBERSHIP_OP_TYPES.has(String(op?.op_type || "")),
    )
  ) {
    return;
  }

  after(async (result) => {
    // History actions return null on failure; `undoTo` returns the number of
    // operations actually reverted, including a partial successful walk. A
    // failed undo or redo must leave the current grid untouched.
    const succeeded = name === "undoTo" ? Number(result) > 0 : result != null;
    if (!succeeded || !isCharacterScopedView.value) return;
    if (overlayOpen.value) {
      pendingOverlayGridRefresh.value = true;
      return;
    }
    preserveScrollOnNextFetch.value = true;
    await fetchAllGridImages({ force: true });
  });
});

// ============================================================
// STACK ORDERING + EXPAND / COLLAPSE + REORDER DRAG
// (moved to useStackOrdering composable)
// ============================================================
const {
  expandedStackIds,
  expandedStackMembers,
  expandedStackLoading,
  selectedMultipleStackIds,
  showRemoveFromStack,
  mapGridImages,
  getStackCardStyle,
  getStackBandStyle,
  getStackBadgeTint,
  isStackExpandedForImage,
  rebuildGridImagesFromLastFetch,
  refreshExpandedStacksAfterFetch,
  loadExpandedStacksInView,
  expandAllStacks,
  collapseAllStacks,
  toggleStackExpand,
  prefetchStackMembers,
  emitStackStats,
  syncExpandAllStacksFromFetchedImages,
  handleStackReorderDragOver,
  handleStackReorderDragLeave,
  handleStackReorderDrop,
  createStackFromSelection,
  dissolveSelectedStacks,
  removeSelectedFromStack,
  createStacksFromSelectedGroups,
  collapseStackImages,
  getLocalStackMembers,
  ensureStackMembersLoaded,
} = useStackOrdering(
  {
    allGridImages,
    lastFetchedGridImages,
    loadedRanges,
    visibleStart,
    visibleEnd,
    renderBuffer,
    divisibleViewWindow,
    stackReorderDrag,
    stackReorderHoverId,
    stackReorderHoverSide,
    setStackReorderHoverId,
    setStackReorderHoverSide,
    selectedImageIds,
    preserveScrollOnNextFetch,
  },
  props,
  emit,
  {
    // useGridScoring is created below this call (it needs the stack helpers
    // this one returns), so the reference is deferred rather than passed by
    // value. Same shape either way from the callee's side.
    invalidateVisibleThumbnailRanges: () => invalidateVisibleThumbnailRanges(),
    updateVisibleThumbnails,
    debouncedFetchAllGridImages,
    fetchThumbnailsForRangeNow,
    maybeRefreshThumbnailsForRange,
    markVisibleFetchSuppressedForExpand,
    clearSelection,
    getPendingRanges: () => pendingRanges,
    setPendingRanges: (v) => {
      pendingRanges = v;
    },
  },
);

// Resolve circular dependency: wire _stackOps with the real functions now
// that useStackOrdering has returned them.
_stackOps.collapseStackImages = collapseStackImages;
_stackOps.mapGridImages = mapGridImages;
_stackOps.syncExpandAllStacksFromFetchedImages =
  syncExpandAllStacksFromFetchedImages;
_stackOps.refreshExpandedStacksAfterFetch = refreshExpandedStacksAfterFetch;

const selectedGroupName = ref("");

async function updateSelectedGroupName() {
  let name = "";
  if (
    selectionStore.selectedCharacter &&
    selectionStore.selectedCharacter !== `${ALL_PICTURES_ID}` &&
    selectionStore.selectedCharacter !== `${UNASSIGNED_PICTURES_ID}` &&
    selectionStore.selectedCharacter !== `${SCRAPHEAP_PICTURES_ID}`
  ) {
    try {
      const char = await getCharacter(selectionStore.selectedCharacter);
      name = char.name || "";
    } catch (e) {
      console.error("Character fetch failed:", e);
    }
  } else if (hasSetSelection.value) {
    if (isSetOverlapView.value) {
      selectedGroupName.value = `Set Overlap (${normalizedSelectedSetIds.value.length})`;
      return;
    }
    try {
      const set = await getPictureSet(primarySelectedSetId.value);
      name = set.set.name || "";
    } catch (e) {
      console.error("Set fetch failed:", e);
    }
  }
  selectedGroupName.value = name;
}

watch(
  [
    () => selectionStore.selectedCharacter,
    () => selectionStore.selectedSet,
    () => selectionStore.selectedSetIds,
  ],
  () => {
    updateSelectedGroupName();
  },
  { immediate: true },
);

const {
  setScore,
  setGuestScore,
  handleGuestConsentAccepted,
  handleGuestConsentRejected,
  initGuestSession,
  isSmartScoreSortActive,
  getGridSmartScoreValue,
  invalidateVisibleThumbnailRanges,
  repositionImageByScore,
  repositionImageBySmartScore,
  insertGridImagesById,
  refreshSmartScoreForImage,
  applyScore,
  applyScoresForSelection,
} = useGridScoring({
  backendUrl: props.backendUrl,
  allGridImages,
  lastFetchedGridImages,
  loadedRanges,
  visibleStart,
  visibleEnd,
  renderBuffer,
  imagesLoading,
  overlayOpen,
  pendingOverlayGridRefresh,
  preserveScrollOnNextFetch,
  skipNextWsRefresh,
  gridContainer,
  guestSessionId,
  guestConsentState,
  guestScoreMap,
  guestConsentBannerVisible,
  pendingGuestScoreIntent,
  emit,
  debouncedFetchAllGridImages,
  fetchImageInfo,
  rebuildGridImagesFromLastFetch,
  triggerNewImageHighlight,
  updateVisibleThumbnails,
  maybeRefreshOverlayForComfyui,
  removeImagesById,
});

// ============================================================
// KEYBOARD NAVIGATION
// (moved to useGridKeyboardNav composable)
// ============================================================
const { onGlobalKeyPress, handleKeyDown } = useGridKeyboardNav(
  {
    scrollWrapper,
    allGridImages,
    rowHeight,
    visibleStart,
    overlayOpen,
    reviewOverlayOpen,
    showSelectionBar,
    searchResultsActive,
    selectedImageIds,
    lastSelectedImageId,
    cursorIdx,
    isMultiCharacterView,
    isSetOverlapView,
    hoveredImageIdx,
    toolbarSelectionMenuOpen,
    isJustifiedMode,
    justifiedLayout,
    isGhosted: isGridIndexGhosted,
  },
  props,
  emit,
  {
    clearFaceSelection,
    clearSearchQuery,
    scrollCursorIntoView,
    focusCursor: focusGridCursor,
    openOverlay,
    deleteSelected,
    selectionBarRef,
    applyScoresForSelection,
    setScore,
  },
);

// ============================================================
// OVERLAY FUNCTIONS
// ============================================================
async function fetchImageInfo(imageId, options = {}) {
  try {
    return await getPictureMetadata(imageId, {
      smartScore: !!options.smartScore,
      cacheBuster: options.force ? Date.now() : undefined,
    });
  } catch (e) {
    console.error("Tag fetch failed:", e);
    return [];
  }
}

function invalidateThumbnailIndex(index) {
  loadedRanges.value = loadedRanges.value.filter(
    ([rangeStart, rangeEnd]) => index < rangeStart || index >= rangeEnd,
  );
}

async function refreshGridImage(imageId, options = {}) {
  if (!imageId) return;
  const dId = getPictureId(imageId);
  const idx = allGridImages.value.findIndex(
    (img) => getPictureId(img?.id) === dId,
  );
  if (idx === -1) return;
  const latestInfo = await fetchImageInfo(imageId, {
    smartScore: options?.smartScore === true || isSmartScoreSortActive(),
    force: options?.force === true,
  });
  if (latestInfo && !Array.isArray(latestInfo)) {
    const current = allGridImages.value[idx] || {};
    const nextImages = allGridImages.value.slice();
    nextImages[idx] = {
      ...current,
      ...latestInfo,
      idx: current.idx ?? idx,
    };
    allGridImages.value = nextImages;
  }
  if (sortStore.selectedSort === LIKENESS_GROUPS_SORT_KEY) {
    const stackIndex = getStackIndexFromItem(allGridImages.value[idx]);
    if (typeof stackIndex === "number") {
      reorderStackByScore(stackIndex);
    }
  }
  invalidateThumbnailIndex(idx);
  fetchThumbnailsBatch(idx, idx + 1);
}

// ── Stack badges after a lifecycle change ───────────────────────────────────
// How many ids one stack-facet read carries. The URL is a repeated `id=` list,
// so a 2000-stack "Keep cover only" would otherwise build one unsendable query.
const STACK_FACET_CHUNK = 200;

/**
 * Re-read the LIVE member count of every stack these pictures belong to and put
 * it back on the mounted cards.
 *
 * This exists because `stack_count` is derived and listing-only: the server
 * computes it per stack over live members (`_enrich_stack_counts`) and
 * `GET /pictures/{id}/metadata` does not carry it at all, so `refreshGridImage`
 * cannot repair a stack badge. That is why a "Keep cover only" left its cover
 * rendering "5" with four of its members already in the Scrapheap.
 *
 * The read is per STACK, not per picture: a `fields=grid` listing represents
 * each stack by the lowest-positioned member inside the id filter and reports
 * that stack's count, so one row repairs every mounted member of it. That is
 * what lets one call serve both directions: the covers after a collapse, and,
 * from the restored copies' own ids, the same covers again after an undo.
 *
 * FIELDS ONLY: nothing is inserted, removed or reordered. That is the property
 * that makes it safe inside a live ghost window, where
 * `debouncedFetchAllGridImages()` is not: a refetch rebuilds the grid without
 * the scrapheaped copies and takes the ghosted tiles off the screen, and with
 * them the one-click undo they advertise.
 *
 * @param {Array<number|string>} pictureIds - any members of the stacks to fix.
 * @returns {Promise<void>}
 */
async function refreshStackFacets(pictureIds) {
  const wanted = [
    ...new Set(
      (Array.isArray(pictureIds) ? pictureIds : [])
        .map((id) => getPictureId(id))
        .filter((id) => id !== null),
    ),
  ];
  if (!wanted.length) return;

  const countByStackId = new Map();
  for (let i = 0; i < wanted.length; i += STACK_FACET_CHUNK) {
    const chunk = wanted.slice(i, i + STACK_FACET_CHUNK);
    let rows;
    try {
      rows = await listPicturesByIds(chunk, {
        fields: "grid",
      });
    } catch (e) {
      console.error(
        `refreshStackFacets: could not re-read the stacks of pictures [${chunk.join(", ")}]; ` +
          "their stack badges keep the count they were last told",
        e,
      );
      continue;
    }
    for (const row of Array.isArray(rows) ? rows : []) {
      const stackId = getPictureStackId(row);
      if (!stackId) continue;
      const count = Number(row?.stack_count ?? row?.stackCount);
      if (!Number.isFinite(count) || count <= 0) continue;
      countByStackId.set(stackId, count);
    }
  }
  if (!countByStackId.size) return;

  let changed = false;
  const source = Array.isArray(lastFetchedGridImages.value)
    ? lastFetchedGridImages.value
    : [];
  const next = source.map((img) => {
    const stackId = getPictureStackId(img);
    if (!stackId || !countByStackId.has(stackId)) return img;
    const count = countByStackId.get(stackId);
    if (getStackBadgeCount(img) === count) return img;
    changed = true;
    // Both spellings. The fetched row carries `stack_count`; `collapseStackImages`
    // writes the card's `stackCount`, which would otherwise win over the fresh
    // value on the next rebuild.
    return { ...img, stack_count: count, stackCount: count };
  });
  // No rebuild when nothing moved: the rebuild reassigns `allGridImages`, which
  // several watchers read as "the grid changed under you".
  if (!changed) return;
  lastFetchedGridImages.value = next;
  rebuildGridImagesFromLastFetch();
}

function getStackIndexFromItem(item) {
  if (!item) return null;
  if (typeof item.stackIndex === "number") return item.stackIndex;
  if (typeof item.stack_index === "number") return item.stack_index;
  return null;
}

function reorderStackByScore(stackIndex) {
  const items = allGridImages.value.slice();
  const stackItems = items.filter(
    (item) => getStackIndexFromItem(item) === stackIndex,
  );
  if (stackItems.length <= 1) return;
  stackItems.sort((a, b) => {
    const scoreA = a?.score ?? 0;
    const scoreB = b?.score ?? 0;
    if (scoreA !== scoreB) return scoreB - scoreA;
    const smartA = a?.smartScore ?? 0;
    const smartB = b?.smartScore ?? 0;
    if (smartA !== smartB) return smartB - smartA;
    return (a?.id ?? 0) - (b?.id ?? 0);
  });
  const result = [];
  let inserted = false;
  for (const item of items) {
    const idx = getStackIndexFromItem(item);
    if (idx === stackIndex) {
      if (inserted) continue;
      result.push(...stackItems);
      inserted = true;
      continue;
    }
    result.push(item);
  }
  for (let i = 0; i < result.length; i += 1) {
    result[i].idx = i;
  }
  allGridImages.value = result;
  invalidateVisibleThumbnailRanges();
}

function handleOverlayChange(payload) {
  if (!payload) return;
  const imageId = payload.imageId ?? payload.id ?? payload;
  const fields = payload.fields || {};
  if (fields.stack) {
    if (overlayOpen.value) {
      // A stack change while overlaying would replace allGridImages, breaking
      // the filmstrip. Defer the full refetch until the overlay closes.
      pendingOverlayGridRefresh.value = true;
      return;
    }
    preserveScrollOnNextFetch.value = true;
    void fetchAllGridImages();
    return;
  }
  if (!imageId) return;
  // The picture's own bytes changed (an in-place rotate from the lightbox).
  // Nothing is inserted, removed or reordered - the card is the same card - but
  // both its shape and its bitmap move, from two different reads, and they have
  // to land together or the tile turns twice on screen. `applyRotatedCards`
  // owns that; see its docstring.
  if (fields.pixels) {
    void applyRotatedCards([imageId]);
    return;
  }
  if ((fields.tags || fields.smartScore) && isSmartScoreSortActive()) {
    if (overlayOpen.value) {
      // Smart-score re-ranking would reorder allGridImages mid-viewing, so the
      // re-rank is deferred to overlay close. Record WHICH picture changed rather
      // than raising the blanket pendingOverlayGridRefresh: closeOverlay can then
      // reposition just these cards (refreshSmartScoreForImage fetches the true
      // score and moves the card to the server's slot) instead of re-sorting the
      // whole library. The full reload produced the same final order at the cost
      // of a complete re-score of every candidate, and it ran *in addition* to the
      // opportunistic move the realtime-sync path performs for the same edit.
      pendingOverlaySmartScoreIds.add(imageId);
      refreshGridImage(imageId);
      return;
    }
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages({ force: true, showProgress: true });
    return;
  }
  refreshGridImage(imageId);
}

async function openOverlay(img) {
  if (!img || !img.id) return;
  markStart("pixlstash:interaction-open-picture");
  overlayInitialExpandedStackIds.value = Array.from(
    expandedStackIds.value || [],
  );
  overlayImageId.value = img.id;
  overlayOpen.value = true;
  _pushOverlayRoute(img.id);
  markEnd("pixlstash:interaction-open-picture");
}

function closeOverlay() {
  overlayOpen.value = false;
  overlayImageId.value = null;
  overlayInitialExpandedStackIds.value = [];
  _removeOverlayRoute();
  if (comfyuiRunner.value?.comfyuiPendingOverlayRefresh) {
    comfyuiRunner.value.comfyuiPendingOverlayRefresh.value = false;
  }
  // A deferred smart-score re-rank is reconciled by moving just the affected
  // cards. Only when no broader refresh is already queued - a full reload
  // re-sorts everything anyway, so repositioning first would be wasted work (and
  // the reload's own resetThumbnailState would discard it).
  const deferredSmartScoreIds = Array.from(pendingOverlaySmartScoreIds);
  pendingOverlaySmartScoreIds.clear();
  const hasBroaderRefresh =
    pendingGridImages.value !== null ||
    pendingTagFilterRefresh.value ||
    pendingOverlayGridRefresh.value;
  if (deferredSmartScoreIds.length && !hasBroaderRefresh) {
    if (deferredSmartScoreIds.length > MAX_DEFERRED_SMART_SCORE_REPOSITIONS) {
      pendingOverlayGridRefresh.value = true;
    } else {
      for (const id of deferredSmartScoreIds) {
        void refreshSmartScoreForImage(id);
      }
    }
  }
  if (pendingGridImages.value !== null) {
    // A background fetch completed while the overlay was open. Apply its
    // result now that we're safe to update the grid.
    allGridImages.value = pendingGridImages.value;
    pendingGridImages.value = null;
    pendingTagFilterRefresh.value = false;
    pendingOverlayGridRefresh.value = false;
    // loadedRanges was repopulated for the OLD images while the overlay was
    // open (after resetThumbnailState() cleared it during the fetch). Now
    // that new images occupy the same indices we must invalidate those ranges
    // so updateVisibleThumbnails() fetches thumbnails for the new images.
    invalidateVisibleThumbnailRanges();
    // The pending images are collapsed (no member rows). Rebuild expanded
    // stacks so any stacks that gained/changed members are correctly
    // re-inserted instead of staying on infinite placeholder.
    void refreshExpandedStacksAfterFetch();
  } else if (pendingTagFilterRefresh.value || pendingOverlayGridRefresh.value) {
    pendingTagFilterRefresh.value = false;
    pendingOverlayGridRefresh.value = false;
    lastFetchSuccess.value = { key: "", at: 0 };
    lastFetchError.value = { key: "", at: 0 };
    // Preserve scroll, exactly as the non-deferred siblings of this refresh do
    // (see the fields.stack / smart-score branches in handlePictureChanged).
    // Those set the flag right before fetching, but when the overlay is open they
    // only raise pendingOverlayGridRefresh and return - so the flag never got set
    // and the deferred fetch ran as a non-preserving one. That resets
    // visibleStart/visibleEnd to the top of the list (useGridFetch) while
    // scrollTop stays where the user left it, so the grid renders the first
    // screenful of cards far above the viewport and looks blank until any scroll
    // recomputes the window.
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages();
  }
}

// ============================================================
// GRID FETCH FUNCTIONS
// ============================================================
function maybeRefreshThumbnailsForRange(start, end) {
  const renderStartValue = renderStart.value;
  const renderEndValue = renderEnd.value;
  if (end <= renderStartValue || start >= renderEndValue) return;
  updateVisibleThumbnails();
}

function fetchThumbnailsForRangeNow(start, end, reason = "manual-now") {
  if (!Number.isFinite(start) || !Number.isFinite(end)) return;
  const safeStart = Math.max(0, Math.floor(start));
  const safeEnd = Math.max(safeStart, Math.floor(end));
  if (safeEnd <= safeStart) return;

  void fetchThumbnailsBatch(safeStart, safeEnd, { reason, force: true });
}

function _resetGridState() {
  gridReady.value = false;
  emptyStateDelayPassed.value = false;
  resetThumbnailState();
  allGridImages.value = [];
  lastFetchedGridImages.value = [];
  expandedStackIds.value = new Set();
  expandedStackMembers.value = new Map();
  expandedStackLoading.value = new Set();
  selectedImageIds.value = [];
  lastSelectedImageId.value = null;
  initialRender.value = true;
}

watch(
  [
    () => selectionStore.selectedCharacter,
    () => selectionStore.selectedSet,
    () => selectionStore.selectedSetIds,
    () => selectionStore.characterMultiMode,
    () => selectionStore.setMultiMode,
    () => selectionStore.setDifferenceBaseId,
    () => projectStore.projectViewMode,
    () => projectStore.selectedProjectId,
    () => searchStore.searchQuery,
    () => sortStore.selectedSort,
    () => sortStore.selectedDescending,
    () => sortStore.selectedSimilarityCharacter,
    () => sortStore.stackThreshold,
  ],
  (next, prev) => {
    // Dropping the armed searches has to happen HERE, before the refetch below,
    // and not in a watcher of its own: `fetchAllGridImages` picks its fetchMode
    // synchronously, so a later watcher would clear the search only after this
    // fetch had already re-run it. See `dropSearchesForViewChange`.
    //
    // Only on an actual view change. This watcher also fires for sort, search
    // text and filter changes, none of which are a reason to throw a search
    // away; indices 0 and 1 are selectedCharacter and selectedSet.
    if (next[0] !== prev?.[0] || next[1] !== prev?.[1]) {
      dropSearchesForViewChange(next[0], next[1]);
    }
    if (scrollWrapper.value) scrollWrapper.value.scrollTop = 0;
    _resetGridState();
    updateSelectedGroupName();
    fetchAllPicturesCount();
    debouncedFetchAllGridImages.cancel();
    fetchAllGridImages({ force: true, showProgress: true });
  },
);

watch(
  [
    () => filterStore.mediaTypeFilter,
    () => filterStore.comfyuiModelFilter,
    () => filterStore.comfyuiLoraFilter,
    () => filterStore.minScoreFilter,
    () => filterStore.maxScoreFilter,
    () => filterStore.unscoredOnlyFilter,
    () => filterStore.smartScoreBucketFilter,
    () => filterStore.resolutionBucketFilter,
    () => filterStore.tagFilter,
    () => filterStore.tagRejectedFilter,
    () => filterStore.tagConfidenceAboveFilter,
    () => filterStore.tagConfidenceBelowFilter,
    () => filterStore.faceBboxFilter,
    () => filterStore.impossibleSources,
    () => filterStore.stackStateFilter,
    () => filterStore.sharedOnlyFilter,
    () => filterStore.unassignedOnlyFilter,
  ],
  () => {
    _resetGridState();
    visibleStart.value = 0;
    visibleEnd.value = 0;
    fetchAllGridImages({ force: true, showProgress: true }).then(() => {
      updateVisibleThumbnails();
    });
  },
);

watch(
  () => gridStore.showStacks,
  async (expandAllStacksEnabled) => {
    if (expandAllStacksEnabled) {
      syncExpandAllStacksFromFetchedImages();
    } else {
      expandedStackIds.value = new Set();
    }
    rebuildGridImagesFromLastFetch();
    await refreshExpandedStacksAfterFetch();
  },
);

watch(
  () => gridStore.columns,
  async () => {
    updateRowHeightFromGrid();
    recalculateVisibleRange();
    await nextTick();
    triggerFaceOverlayRedraw();
    requestAnimationFrame(() => {
      triggerFaceOverlayRedraw();
    });
  },
);

watch(
  () => gridStore.compactMode,
  () => {
    updateRowHeightFromGrid();
    updateVisibleThumbnails();
  },
);

// Switching square <-> justified changes the whole row model (uniform grid vs
// packed rows), so the geometry must be recomputed at once - otherwise the mode
// only takes effect after an unrelated relayout (a resize, or the full refresh
// the maintainer hit). Mirror the columns watch: re-measure row height, re-pack,
// recompute the visible range, and refetch the now-visible thumbnails.
watch(
  () => gridStore.thumbnailMode,
  async () => {
    updateRowHeightFromGrid();
    recalculateVisibleRange();
    updateVisibleThumbnails();
    await nextTick();
    triggerFaceOverlayRedraw();
    requestAnimationFrame(() => {
      triggerFaceOverlayRedraw();
    });
  },
);

// ============================================================
// THUMBNAIL TRACKING STATE
// ============================================================
// Debounce timer for scroll-triggered fetches
let thumbFetchTimeout = null;
const thumbnailRequestEpoch = ref(0);
const suppressVisibleThumbFetch = ref({
  until: 0,
  start: 0,
  end: 0,
});

function shouldSuppressVisibleWindowFetch(start, end) {
  const now = Date.now();
  const token = suppressVisibleThumbFetch.value;
  if (!token || now > Number(token.until || 0)) return false;
  if (!isRangeOverlap(start, end, token.start, token.end)) return false;

  const visible = allGridImages.value.slice(start, end);
  const missingOutsideSuppressed = visible.some((img, idx) => {
    if (!img || img.thumbnail) return false;
    const globalIndex = start + idx;
    return globalIndex < token.start || globalIndex >= token.end;
  });
  return !missingOutsideSuppressed;
}

function markVisibleFetchSuppressedForExpand(start, end) {
  suppressVisibleThumbFetch.value = {
    until: Date.now() + 350,
    start,
    end,
  };
}

function resetThumbnailState() {
  loadedRanges.value = [];
  pendingRanges = [];
  suppressVisibleThumbFetch.value = {
    until: 0,
    start: 0,
    end: 0,
  };
  if (thumbFetchTimeout) {
    clearTimeout(thumbFetchTimeout);
    thumbFetchTimeout = null;
  }
  thumbnailRequestEpoch.value += 1;
  for (const key of Object.keys(thumbnailLoadedMap)) {
    delete thumbnailLoadedMap[key];
  }
  for (const key of Object.keys(thumbnailAssignedAtMap)) {
    delete thumbnailAssignedAtMap[key];
  }
  for (const timer of thumbnailRetryTimers.values()) {
    clearTimeout(timer);
  }
  thumbnailRetryTimers.clear();
  for (const key of Object.keys(thumbnailRetryCounts)) {
    delete thumbnailRetryCounts[key];
  }
}

watch(
  [() => expandedStackIds.value, () => lastFetchedGridImages.value],
  () => {
    emitStackStats();
  },
  { immediate: true },
);

watch(
  [() => gridStore.showFaceBboxes, () => allGridImages.value.length],
  ([faceEnabled, length], [prevFace, prevLength]) => {
    if (!faceEnabled) return;
    if (length <= 0) return;
    if (faceEnabled === prevFace && length === prevLength) {
      return;
    }
    invalidateVisibleThumbnailRanges();
  },
);

watch(
  [() => gridStore.showDetections, () => allGridImages.value.length],
  ([detEnabled, length], [prevDet, prevLength]) => {
    if (!detEnabled) return;
    if (length <= 0) return;
    if (detEnabled === prevDet && length === prevLength) {
      return;
    }
    // Re-fetch visible thumbnails so detection boxes (carried in the batch
    // thumbnail response) populate when the overlay is switched on.
    invalidateVisibleThumbnailRanges();
  },
);

// ============================================================
// MEDIA FILTERING + EMPTY STATE
// ============================================================
function filterImagesByMediaType(images) {
  let filtered = images;
  if (filterStore.mediaTypeFilter === "images") {
    filtered = filtered.filter((img) => {
      if (!img) return false;
      const candidates = [img.name, img.id, img.format]
        .filter(Boolean)
        .map((v) => (typeof v === "string" ? v : ""));
      return candidates.some((val) => isSupportedImageFile(val));
    });
  } else if (filterStore.mediaTypeFilter === "videos") {
    filtered = filtered.filter((img) => {
      if (!img) return false;
      const candidates = [img.name, img.id, img.format]
        .filter(Boolean)
        .map((v) => (typeof v === "string" ? v : ""));
      return candidates.some((val) => isSupportedVideoFile(val));
    });
  }
  return filtered;
}

const filteredGridCount = computed(() => {
  if (!allGridImages.value) return 0;
  return filterImagesByMediaType(allGridImages.value).length;
});

const EMPTY_STATE_DELAY_MS = 350;
const emptyStateDelayPassed = ref(false);
let emptyStateDelayTimer = null;

const showEmptyState = computed(() => {
  if (sidebarStore.folderScanning) return false;
  return (
    gridReady.value &&
    !imagesLoading.value &&
    filteredGridCount.value === 0 &&
    emptyStateDelayPassed.value
  );
});

const showFolderScanningState = computed(() => {
  return (
    sidebarStore.folderScanning &&
    gridReady.value &&
    filteredGridCount.value === 0
  );
});

const canShowAllPicturesButton = computed(() => {
  return totalAllPicturesCount.value > 0;
});

// The library holds nothing at all, as opposed to "the filter matched nothing"
// or "the scrap heap is empty". Those are different questions with different
// answers and keep the card they had; this one is the first screen of an
// install, and it gets the three routes out.
//
// Two guards beyond the count, because the count alone is not the claim:
//
//   * `totalAllPicturesCountLoaded` - the count starts at 0 and its fetch
//     swallows failures, so an unanswered request looks exactly like an empty
//     library. Saying "This library is empty" over a backend that did not reply
//     is worse than saying nothing, and this screen says it with three buttons.
//   * `!isReadOnly` - a share recipient is not the owner of anything here.
//     Every route out leads somewhere they cannot go (two open the owner's
//     sidebar dialogs; the importer refuses a read-only token outright), and
//     the count they get is the one the summary route refused them, not a fact
//     about the library. They keep the plain card.
const showLibraryEmptyState = computed(
  () =>
    showEmptyState.value &&
    totalAllPicturesCountLoaded.value &&
    totalAllPicturesCount.value === 0 &&
    !isReadOnly.value &&
    !isScrapheapView.value &&
    !isSetOverlapView.value,
);

watch(showLibraryEmptyState, (shown) => {
  if (shown) emit("library-empty");
});

// The first count answers whether this is a first run. App.vue holds the
// telemetry question until it knows, so a fresh install meets the library
// before it meets the privacy dialog.
watch(totalAllPicturesCountLoaded, (loaded) => {
  if (loaded) emit("library-loaded", { empty: showLibraryEmptyState.value });
});

/**
 * Files chosen through the empty library's "Add files…".
 *
 * The same filter and the same notice the two drop paths use
 * (`useGridDragDrop`, `useWindowFileImport`) - an OS picker's `accept` is
 * advisory, so a button that skipped this would be the one import route that
 * accepts anything and says nothing. The project comes from App.vue, which
 * defaults it to the one being looked at.
 */
function importChosenFiles(chosen) {
  // `Array.from` rather than a bare `.filter`: the one caller emits a real
  // Array today, but the natural thing to hand a function named this is the
  // `FileList` off an `<input>`, which has no `.filter` and would throw here
  // rather than anywhere near the mistake. It also gives `offered` a length to
  // compare against below, so nothing reads the argument twice.
  const offered = Array.from(chosen ?? []);
  const files = offered.filter((file) => isSupportedImportFile(file));
  if (!files.length) {
    noticeStore.warning(
      "None of those files are a supported image, video or archive.",
      { key: "import-unsupported-files" },
    );
    return;
  }
  if (files.length !== offered.length) {
    noticeStore.warning(
      "Some of those files are not a supported image, video or archive, and were skipped.",
      { key: "import-unsupported-files" },
    );
  }
  emit("local-import", { files });
}

const emptyStateTitle = computed(() => {
  if (isSetOverlapView.value) {
    return "No overlap";
  }
  if (isScrapheapView.value) {
    return "No pictures in the scrap heap";
  }
  // The `totalAllPicturesCount === 0` arm is what `LibraryEmptyState` now
  // answers; this branch is only reached for a view that has its own empty
  // question (scrap heap, set overlap) or a filter that matched nothing.
  return "No pictures match the current filters";
});

const emptyStateSubtitle = computed(() => {
  if (isSetOverlapView.value) {
    return "The picture sets have no overlap.";
  }
  if (isScrapheapView.value) {
    return "Are all your pictures that good?";
  }
  return "Try clearing filters, adjusting your search, or switching sets.";
});

const emptyStateImage = computed(() => {
  return isScrapheapView.value ? "/EmptyTrash.png" : "/Empty.png";
});

const isDarkThemeActive = computed(() => {
  const mode = String(userPrefsStore.themeMode || "dark").toLowerCase();
  if (mode === "dark") return true;
  if (mode === "light") return false;
  if (mode === "system") {
    return (
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
    );
  }
  return false;
});

const emptyStateImageStyle = computed(() => {
  if (!isDarkThemeActive.value) return {};
  return {
    filter: "invert(1) brightness(1.08) contrast(0.92)",
  };
});

watch([imagesLoading, filteredGridCount], ([loading, count]) => {
  if (emptyStateDelayTimer) {
    clearTimeout(emptyStateDelayTimer);
    emptyStateDelayTimer = null;
  }

  if (loading || count > 0 || sidebarStore.folderScanning) {
    emptyStateDelayPassed.value = false;
    return;
  }

  emptyStateDelayPassed.value = false;
  emptyStateDelayTimer = setTimeout(() => {
    if (
      !imagesLoading.value &&
      filteredGridCount.value === 0 &&
      !sidebarStore.folderScanning
    ) {
      emptyStateDelayPassed.value = true;
    }
  }, EMPTY_STATE_DELAY_MS);
});

// When scanning ends the grid will reload; reset the delay so the
// empty state only shows after images have had a chance to arrive.
watch(
  () => sidebarStore.folderScanning,
  (scanning) => {
    if (!scanning) {
      if (emptyStateDelayTimer) {
        clearTimeout(emptyStateDelayTimer);
        emptyStateDelayTimer = null;
      }
      emptyStateDelayPassed.value = false;
    }
  },
);

const gridImagesToRender = computed(() => {
  if (!allGridImages.value) {
    console.warn("allGridImages is undefined");
    return [];
  }

  const filtered = filterImagesByMediaType(allGridImages.value);
  return filtered.slice(renderStart.value, renderEnd.value);
});

const imageCardRefs = new Map();
const firstFocusableRenderedIndex = computed(
  () =>
    gridImagesToRender.value.find(
      (image) => image?.id && !isImageGhosted(image),
    )?.idx ?? -1,
);

function setImageCardRef(index, element) {
  const normalizedIndex = Number(index);
  if (!Number.isFinite(normalizedIndex)) return;
  if (element) imageCardRefs.set(normalizedIndex, element);
  else imageCardRefs.delete(normalizedIndex);
}

function imageCardTabIndex(image) {
  return pictureGridTabIndex(image, {
    cursorIndex: cursorIdx.value,
    fallbackIndex: firstFocusableRenderedIndex.value,
    ghosted: isImageGhosted(image),
  });
}

function imageCardAriaLabel(image) {
  return pictureGridLabel(image, { video: isVideo(image) });
}

async function focusGridCursor(index) {
  await nextTick();
  imageCardRefs.get(Number(index))?.focus({ preventScroll: true });
}

function handleImageCardFocus(image) {
  if (image?.id && !isImageGhosted(image)) cursorIdx.value = image.idx;
}

// Batch fetch metadata (including thumbnail) for visible range
// ============================================================
// THUMBNAIL BATCH FETCH
// ============================================================
async function fetchThumbnailsBatch(start, end, meta = {}) {
  if (start === undefined || start === null) {
    start = renderStart.value;
  }
  if (end === undefined || end === null) {
    end = renderEnd.value;
  }

  const requestEpoch = thumbnailRequestEpoch.value;

  if (rangeCovers(pendingRanges, start, end)) {
    return;
  }
  if (!meta?.force && rangeCovers(loadedRanges.value, start, end)) {
    return;
  }
  pendingRanges.push([start, end]);
  // Fetch batch metadata for visible range
  try {
    let images = [];
    let ids = [];
    // Use allGridImages directly regardless of whether we're in a picture-set
    // view. The picture-set endpoint returns a flat leader-only list and
    // doesn't know about expanded stack members; slicing it with absolute
    // indices would overwrite expanded members with wrong images and leave
    // placeholder thumbnails permanently broken.
    images = allGridImages.value.slice(start, end);
    // Prepare grid image objects
    const gridImages = images.map((img, idx) => ({
      ...img,
      score: img.score ?? 0,
      idx: start + idx, // Ensure idx is global index
      thumbnail: img?.thumbnail ?? null,
      faces: Array.isArray(img?.faces) ? img.faces : [],
      detections: Array.isArray(img?.detections) ? img.detections : [],
      penalised_tags: Array.isArray(img?.penalised_tags)
        ? img.penalised_tags
        : [],
      thumbnail_width: img?.thumbnail_width,
      thumbnail_height: img?.thumbnail_height,
      square_crop_x: img?.square_crop_x,
      square_crop_y: img?.square_crop_y,
      square_crop_side: img?.square_crop_side,
    }));
    // Synchronously pre-fill thumbnail URLs from imported_at so <img> elements
    // render immediately without waiting for the POST round trip. This is a
    // placeholder with a placeholder's lifetime: the POST below replaces it
    // with the server's own URL the moment that answers, because only the
    // server's `?v=` token moves when a bitmap is regenerated. The POST also
    // enriches face overlays and penalised-tag hints.
    for (let i = 0; i < gridImages.length; i++) {
      const gridImg = gridImages[i];
      if (!gridImg.thumbnail && gridImg.id && gridImg.imported_at) {
        const v = Math.floor(new Date(gridImg.imported_at).getTime() / 1000);
        const rawUrl = `/pictures/thumbnails/${gridImg.id}.webp?v=${v}`;
        gridImg.thumbnail = appendShareToken(
          rawUrl.startsWith("http") ? rawUrl : `${props.backendUrl}${rawUrl}`,
        );
        allGridImages.value[start + i] = {
          ...allGridImages.value[start + i],
          thumbnail: gridImg.thumbnail,
        };
      }
    }
    ids = Array.from(
      new Set(
        gridImages
          .filter((img) => img.id !== null && img.id !== undefined)
          .map((img) => String(img.id)),
      ),
    );
    let overlayNeedsRedraw = false;
    if (ids.length) {
      const thumbData = await getThumbnails(ids);
      if (requestEpoch !== thumbnailRequestEpoch.value) {
        return;
      }
      const requestedIds = new Set(ids);
      for (const gridImg of gridImages) {
        if (!requestedIds.has(String(gridImg.id))) {
          continue;
        }
        const thumbObj = thumbData[String(gridImg.id)];
        // The server owns the thumbnail URL. Its `?v=` token is the only thing
        // that moves when a bitmap is regenerated - the upgrade NULL-reset in
        // thumbnail_generation_task, a reference-folder source swap, an in-place
        // rotate - so the answer always replaces whatever the card is carrying,
        // including the `?v=<imported_at>` placeholder pre-filled just above.
        // That placeholder is a stand-in until this arrives, never a
        // replacement for it: gating on "didn't already have one" meant every
        // card with an imported_at kept a token derived from its import date,
        // which never changes again, and no regenerated thumbnail ever
        // repainted. A card the server reports no URL for keeps what it has,
        // so a still-processing picture is not blanked; a card that never had
        // one stays null and goes down the retry path below.
        const thumbnailUrl =
          thumbObj && thumbObj.thumbnail ? thumbObj.thumbnail : null;
        if (thumbnailUrl) {
          const previousThumbnail = gridImg.thumbnail || null;
          gridImg.thumbnail = appendShareToken(
            thumbnailUrl.startsWith("http")
              ? thumbnailUrl
              : `${props.backendUrl}${thumbnailUrl}`,
          );
          if (gridImg.id != null) {
            if (gridImg.thumbnail !== previousThumbnail) {
              thumbnailAssignedAtMap[gridImg.id] = performance.now();
            }
            thumbnailLoadedMap[gridImg.id] =
              (thumbnailLoadedMap[gridImg.id] || 0) + 1;
          }
        }
        // Faces, thumbnail dimensions and penalised_tags come from the same
        // authoritative record, whether or not it carried a URL.
        if (thumbObj) {
          const thumbWidth = Number(thumbObj.thumbnail_width);
          const thumbHeight = Number(thumbObj.thumbnail_height);
          if (!Number.isNaN(thumbWidth) && thumbWidth > 0) {
            gridImg.thumbnail_width = thumbWidth;
          }
          if (!Number.isNaN(thumbHeight) && thumbHeight > 0) {
            gridImg.thumbnail_height = thumbHeight;
          }
          // Face-weighted square-crop rectangle (bitmap pixel space). Nullable
          // while a picture is still (re)processing - leave undefined so the
          // square render path falls back to object-fit:cover centring until
          // the finder populates them, then upgrades reactively on the next
          // thumbnails fetch.
          // Guard null/undefined explicitly first: Number(null) is 0, which
          // would masquerade as a valid crop origin instead of falling back.
          const cropX =
            thumbObj.square_crop_x == null
              ? NaN
              : Number(thumbObj.square_crop_x);
          const cropY =
            thumbObj.square_crop_y == null
              ? NaN
              : Number(thumbObj.square_crop_y);
          const cropSide =
            thumbObj.square_crop_side == null
              ? NaN
              : Number(thumbObj.square_crop_side);
          gridImg.square_crop_x = Number.isFinite(cropX) ? cropX : undefined;
          gridImg.square_crop_y = Number.isFinite(cropY) ? cropY : undefined;
          gridImg.square_crop_side =
            Number.isFinite(cropSide) && cropSide > 0 ? cropSide : undefined;
        }
        gridImg.faces =
          thumbObj && Array.isArray(thumbObj.faces) ? thumbObj.faces : [];
        if (gridStore.showFaceBboxes && gridImg.faces.length) {
          overlayNeedsRedraw = true;
        }
        gridImg.detections =
          thumbObj && Array.isArray(thumbObj.detections)
            ? thumbObj.detections
            : [];
        if (gridStore.showDetections && gridImg.detections.length) {
          overlayNeedsRedraw = true;
        }
        gridImg.penalised_tags =
          thumbObj && Array.isArray(thumbObj.penalised_tags)
            ? thumbObj.penalised_tags
            : [];
      }
    }
    // Insert/update images at their correct indices
    if (requestEpoch !== thumbnailRequestEpoch.value) {
      return;
    }
    // id → current slot, for results whose slot moved while the batch was in
    // flight. Built once per batch (not per image) so the write-back stays linear.
    // First occurrence wins: an expanded stack can repeat a picture id, and the
    // leader row is the one a positional write would have targeted.
    const movedIndexById = new Map();
    for (let i = 0; i < allGridImages.value.length; i += 1) {
      const existingId = allGridImages.value[i]?.id;
      if (existingId == null) continue;
      const key = String(existingId);
      if (!movedIndexById.has(key)) movedIndexById.set(key, i);
    }
    for (let i = 0; i < gridImages.length; i++) {
      const img = gridImages[i];
      // Skip null-id slots: the snapshot was taken before the grid was fully
      // populated and a concurrent BG batch may have since written real data
      // into this slot. Writing a stale null-id object would wipe that data.
      if (img.id == null) {
        continue;
      }
      // Resolve the picture's CURRENT slot rather than trusting start + i.
      // gridImages was sliced out of allGridImages before the await above, and a
      // smart-score reposition (repositionImageBySmartScore → _spliceAndReinsert)
      // re-orders allGridImages *without* bumping thumbnailRequestEpoch, so the
      // epoch guard cannot catch it. Adding a penalised tag does exactly that: the
      // card moves, and a positional write-back would drop this refreshed
      // penalised_tags payload onto whichever picture now occupies the slot -
      // leaving the just-tagged card with stale data (no problem indicator) and
      // corrupting an unrelated card. The original index is still preferred when it
      // holds the right picture, so the common no-movement case is unchanged.
      let targetIndex = start + i;
      if (String(allGridImages.value[targetIndex]?.id) !== String(img.id)) {
        const moved = movedIndexById.get(String(img.id));
        if (moved === undefined) {
          // Picture left the grid entirely (filtered out, or collapsed into a
          // stack) - nothing to update.
          continue;
        }
        targetIndex = moved;
      }
      img.idx = targetIndex;
      allGridImages.value[targetIndex] = img;
      if (img.thumbnail) {
        clearThumbnailRetry(img.id);
      } else {
        scheduleThumbnailRetry(img.id, targetIndex, requestEpoch);
      }
    }
    loadedRanges.value.push([start, end]);
    if (overlayNeedsRedraw) {
      triggerFaceOverlayRedraw();
    }
  } catch (err) {
    console.error("[BATCH ERROR]", err);
  } finally {
    pendingRanges = pendingRanges.filter(
      ([rangeStart, rangeEnd]) => rangeStart !== start || rangeEnd !== end,
    );
  }
}

// ============================================================
// SCROLL + VIEWPORT UPDATE
// ============================================================
function updateVisibleThumbnails() {
  // Fetch exactly the render window. In square mode renderStart/renderEnd are
  // identical to visibleStart/End ± renderBuffer; in justified mode they are
  // additionally snapped outward to row boundaries, so fetching them keeps the
  // fetch window equal to what is actually painted (anti-blank-tile).
  let start = Math.max(0, renderStart.value);
  let end = Math.min(allGridImages.value.length, renderEnd.value);
  if (shouldSuppressVisibleWindowFetch(start, end)) {
    return;
  }

  // Lazily load members for expanded stacks that have scrolled into view.
  void loadExpandedStacksInView();

  if (rangeCovers(loadedRanges.value, start, end)) return;
  if (rangeCovers(pendingRanges, start, end)) return;

  // Debounce fetches to avoid excessive requests
  if (thumbFetchTimeout) clearTimeout(thumbFetchTimeout);

  const requestEpoch = thumbnailRequestEpoch.value;
  thumbFetchTimeout = setTimeout(async () => {
    thumbFetchTimeout = null;
    if (requestEpoch !== thumbnailRequestEpoch.value) {
      return;
    }
    await fetchThumbnailsBatch(start, end, {
      reason: "visible-window",
    });
  }, 80);
  scheduleSharedPictureFetch();
}

// ============================================================
// CLICK HANDLERS
// ============================================================
function handleImageCardClick(img, idx, event) {
  if (!img.id) return;
  // A ghosted tile is a placeholder for an undo decision, not content. It is
  // already in the Scrapheap, so selecting it would arm the selection bar
  // against a picture no bulk action can act on. Silent: the ghost's own
  // marking already says "not available", and a notice per click is noise.
  if (isImageGhosted(img)) return;
  // Suppress the synthesized click that fires right after a long-press touchend
  if (suppressTouchClickId.value === img.id) {
    suppressTouchClickId.value = null;
    return;
  }
  cursorIdx.value = idx;
  focusGridCursor(idx);
  const isCtrl = event.ctrlKey || event.metaKey;
  const isShift = event.shiftKey;
  let newSelection;
  const allGrid = allGridImages.value;
  const anchorIndex =
    lastSelectedImageId.value != null
      ? allGrid.findIndex(
          (item) =>
            getPictureId(item?.id) === getPictureId(lastSelectedImageId.value),
        )
      : -1;
  if (isCtrl) {
    // Toggle selection
    newSelection = [...selectedImageIds.value];
    if (newSelection.includes(img.id)) {
      newSelection = newSelection.filter((id) => id !== img.id);
    } else {
      newSelection.push(img.id);
    }
    lastSelectedImageId.value = img.id;
  } else if (isShift && anchorIndex >= 0) {
    // Range select: select only the contiguous range between anchor and clicked item
    const start = Math.min(anchorIndex, idx);
    const end = Math.max(anchorIndex, idx);
    newSelection = allGrid
      .slice(start, end + 1)
      .map((i) => i.id)
      .filter(Boolean)
      // Ghosts inside the span are skipped silently: they read visually as
      // excluded cells already, so a selection count that included them would
      // be the surprising half.
      .filter((id) => !isImageGhosted(id));
    // Do NOT merge with previous selection; replace it
  } else if (isShift && anchorIndex < 0) {
    newSelection = [img.id];
    lastSelectedImageId.value = img.id;
  } else {
    // Single click (no ctrl/shift): select only this image
    newSelection = [img.id];
    lastSelectedImageId.value = img.id;
  }
  selectedImageIds.value = newSelection;
}

function handleThumbnailClick(img, idx, event) {
  if (!img.id) return;
  // Never open the lightbox on a ghost. The overlay is the full editing surface
  // and it FREEZES the sequence it opens on for its whole lifetime (§9.1), so
  // opening it on a picture that is seconds from leaving the grid offers a pile
  // of actions against a doomed object and pins a stale filmstrip while it does.
  if (isImageGhosted(img)) {
    event.stopPropagation();
    return;
  }
  // In touch-select mode, the toggle was already handled in handleTouchEnd.
  // Just suppress any synthesized click that slipped through.
  if (touchSelectMode.value) {
    event.stopPropagation();
    return;
  }
  const isCtrl = event.ctrlKey || event.metaKey;
  const isShift = event.shiftKey;
  if (isCtrl || isShift) {
    return handleImageCardClick(img, idx, event);
  }
  // Touch two-tap: first tap selects the image; second tap on the same
  // already-selected image opens the overlay.
  if (lastPointerWasTouch.value) {
    lastPointerWasTouch.value = false;
    const alreadySoleSelection =
      selectedImageIds.value.length === 1 &&
      selectedImageIds.value[0] === img.id;
    if (alreadySoleSelection) {
      openOverlay(img);
    } else {
      selectedImageIds.value = [img.id];
      lastSelectedImageId.value = img.id;
      cursorIdx.value = idx;
    }
    event.stopPropagation();
    return;
  }
  // Desktop: open overlay directly
  openOverlay(img);
  event.stopPropagation();
}

// Clear selection when clicking grid background
function handleGridBackgroundClick(e) {
  if (!e.target.closest(".image-card")) {
    if (touchSelectMode.value) {
      exitTouchSelectMode();
    } else {
      selectedImageIds.value = [];
      lastSelectedImageId.value = null;
      cursorIdx.value = null;
    }
  }
}

// ── Context menu ─────────────────────────────────────────────────────────────

function handleImageContextMenu(img, event) {
  if (!img?.id) return;
  // No menu on a ghost - before the select-on-right-click side effect below,
  // which would otherwise put it in the selection. Every entry acts on the
  // selection, so the menu would be entirely disabled; and the one entry that
  // would make sense, Restore, is a second Undo affordance competing with the
  // receipt already counting down a few seconds away.
  if (isImageGhosted(img)) return;
  if (!selectedImageIds.value.includes(img.id)) {
    selectedImageIds.value = [img.id];
    lastSelectedImageId.value = img.id;
  }
  contextMenuImage.value = img;
  contextMenuClickedFace.value = null;
  contextMenuX.value = event.clientX;
  contextMenuY.value = event.clientY;
  contextMenuVisible.value = true;
}

function handleFaceBboxContextMenu(img, overlay, event) {
  if (!img?.id) return;
  if (!selectedImageIds.value.includes(img.id)) {
    selectedImageIds.value = [img.id];
    lastSelectedImageId.value = img.id;
  }
  contextMenuImage.value = img;
  contextMenuClickedFace.value = overlay.face;
  contextMenuX.value = event.clientX;
  contextMenuY.value = event.clientY;
  contextMenuVisible.value = true;
}

// ── Overlay (lightbox) context menu ─────────────────────────────────────────
// The overlay emits `request-context-menu` (media-area right-click or the
// Shift+F10 / ContextMenu key) with the currently-displayed image object and a
// screen position. Every action below is scoped to THIS one picture - the grid
// selection is never read or persistently mutated.

function handleOverlayContextMenuRequest(payload) {
  const img = payload?.image;
  if (!img?.id) return;
  overlayCtxImage.value = img;
  overlayCtxX.value = payload.clientX ?? 0;
  overlayCtxY.value = payload.clientY ?? 0;
  overlayCtxVisible.value = true;
}

async function handleOverlaySave() {
  await imageOverlayRef.value?.saveMedia?.(overlayCtxImage.value);
}

async function handleOverlaySaveAs() {
  await imageOverlayRef.value?.saveMediaAs?.(overlayCtxImage.value);
}

async function handleOverlayCopy() {
  await imageOverlayRef.value?.copyMedia?.(overlayCtxImage.value);
}

function handleOverlayShare() {
  const img = overlayCtxImage.value;
  if (!img?.id || !img?.format) return;
  // ShareDialog reads `contextMenuImage`; point it at the overlay picture. The
  // grid menu is closed, so this does not affect any grid interaction.
  contextMenuImage.value = img;
  sharePicDialogOpen.value = true;
}

function handleOverlayReverseImageSearch() {
  const id = overlayCtxImage.value?.id;
  if (id == null) return;
  faceLikenessSearchFaceId.value = null;
  reverseImageSearchPictureIds.value = [id];
  // Reveal the results behind the lightbox and clear any text search.
  closeOverlay();
  emit("clear-search", "");
}

function handleOverlayFindSimilarFaces(faceId) {
  if (!faceId) return;
  reverseImageSearchPictureIds.value = [];
  faceLikenessSearchFaceId.value = faceId;
  // Same contract as the overlay's reverse-image-search sibling: the results
  // land in the grid BEHIND the lightbox, and while the overlay is open every
  // grid mutation is deferred (§9.1), so without closing it the action looks
  // like it did nothing at all.
  closeOverlay();
  emit("clear-search", "");
}

function openOverlaySegmentDialog() {
  const id = overlayCtxImage.value?.id;
  if (id == null || isReadOnly.value) return;
  segmentTargetIds.value = [id];
  segmentPrompt.value = "";
  segmentDialogOpen.value = true;
}

async function handleOverlayDelete() {
  const id = overlayCtxImage.value?.id;
  if (id == null) return;
  // Scope the shared delete flow to just this picture (idsOverride). For the
  // scrapheap this opens the Delete-forever confirm; for a normal view it
  // soft-deletes immediately. Either way, close the lightbox so it never shows
  // a picture that has just left the current view.
  await deleteSelected([id]);
  closeOverlay();
}

async function handleOverlayScrapheapRestore() {
  const id = overlayCtxImage.value?.id;
  if (id == null) return;
  try {
    await restoreScrapheap([id]);
  } catch (err) {
    console.error("Failed to restore picture from the scrapheap", err);
    noticeStore.error(`Couldn't restore that picture. ${errorDetail(err)}`, {
      key: "scrapheap-restore-overlay",
    });
  }
  removeImagesById([id]);
  closeOverlay();
  emit("refresh-sidebar");
  fetchAllGridImages().then(() => {
    loadedRanges.value = [];
    updateVisibleThumbnails();
  });
}

function handleContextMenuOpenTagPanel() {
  selectionBarRef.value?.openTagInput();
}

function handleContextMenuOpenPluginPanel() {
  selectionBarRef.value?.openPluginPanel();
}

function handleContextMenuOpenComfyuiPanel() {
  selectionBarRef.value?.openComfyuiPanel();
}

function openSegmentDialog() {
  if (!selectedImageIds.value.length || isReadOnly.value) return;
  segmentTargetIds.value = null;
  segmentPrompt.value = "";
  segmentDialogOpen.value = true;
}

async function confirmSegment() {
  const source = Array.isArray(segmentTargetIds.value)
    ? segmentTargetIds.value
    : selectedImageIds.value;
  const ids = source
    .map((id) => Number(getPictureId(id)))
    .filter((id) => Number.isFinite(id) && id > 0);
  segmentDialogOpen.value = false;
  segmentTargetIds.value = null;
  if (!ids.length || !props.backendUrl) return;
  // Detection runs as a background GPU task. The grid card refreshes in place
  // on the resulting CHANGED_PICTURES event (detections is a card-content
  // field), and the overlay reconciles too if open.
  try {
    await detectPictures(ids, segmentPrompt.value.trim());
    // Nudge the tasks poller so the activity light / Tasks-tab pulse appear
    // within one poll RTT instead of up to the 5 s idle interval later.
    tasksStore.nudge();
  } catch (err) {
    console.error("Object detection request failed:", err);
  }
}

function sharePicture() {
  if (!contextMenuImage.value?.id || !contextMenuImage.value?.format) return;
  sharePicDialogOpen.value = true;
}

function onSharePicCreated() {
  const imgId = contextMenuImage.value?.id;
  if (imgId) {
    sharedPictureIds.value = new Set([...sharedPictureIds.value, imgId]);
  }
}

// ── Shared-picture IDs batch fetch ────────────────────────────────────────

let _sharedIdsFetchTimeout = null;

function scheduleSharedPictureFetch() {
  if (isReadOnly.value) return;
  if (_sharedIdsFetchTimeout) clearTimeout(_sharedIdsFetchTimeout);
  _sharedIdsFetchTimeout = setTimeout(async () => {
    const start = Math.max(0, visibleStart.value - renderBuffer.value);
    const end = Math.min(
      allGridImages.value.length,
      visibleEnd.value + renderBuffer.value,
    );
    const visibleSlice = allGridImages.value.slice(start, end);
    const ids = visibleSlice.map((img) => img.id).filter(Boolean);
    if (!ids.length) return;
    try {
      const body = await getSharedPictureIds(ids);
      const shared = new Set(body?.shared_ids ?? []);
      // Update: remove any id from the queried batch that is no longer shared,
      // and add any that are now shared. This keeps the set accurate when
      // tokens are later revoked.
      const nextShared = new Set(sharedPictureIds.value);
      for (const id of ids) {
        if (shared.has(id)) {
          nextShared.add(id);
        } else {
          nextShared.delete(id);
        }
      }
      sharedPictureIds.value = nextShared;
    } catch (e) {
      // Non-critical: the shared badge just stays as it was. Log it so a
      // persistently failing batch is visible rather than invisible.
      console.debug("Failed to refresh the shared-picture badges", e);
    }
  }, 300);
}

function openRevokeSharesDialog() {
  const img = contextMenuImage.value;
  if (!img?.id) return;
  revokeSharesPending.value = { pictureId: img.id };
  revokeSharesDialogOpen.value = true;
}

async function confirmRevokePictureShares() {
  const pending = revokeSharesPending.value;
  revokeSharesDialogOpen.value = false;
  revokeSharesPending.value = null;
  if (!pending?.pictureId) return;
  try {
    await revokeTokensByResource("picture", pending.pictureId);
    const next = new Set(sharedPictureIds.value);
    next.delete(pending.pictureId);
    sharedPictureIds.value = next;
  } catch (e) {
    console.error("[ImageGrid] Failed to revoke picture shares", e);
  }
}

// ============================================================
// TAGS + METADATA
// ============================================================

// updateColumns removed; columns is now controlled by prop

async function _afterTagMutation(imageId) {
  if (userPrefsStore.applyTagFilter) {
    if (overlayOpen.value) {
      // The overlay is showing live tag state already. Defer the full
      // tag-filtered refetch until the overlay is closed to prevent the
      // grid from unexpectedly going empty in the background.
      pendingTagFilterRefresh.value = true;
      refreshGridImage(imageId);
      return;
    }
    lastFetchSuccess.value = { key: "", at: 0 };
    lastFetchError.value = { key: "", at: 0 };
    await fetchAllGridImages();
    updateVisibleThumbnails();
    return;
  }
  if (isSmartScoreSortActive()) {
    if (overlayOpen.value) {
      // Smart-score re-ranking would reorder the grid while the overlay is
      // open. Defer the full refetch until the overlay closes.
      pendingOverlayGridRefresh.value = true;
      refreshGridImage(imageId);
      return;
    }
    // Smart score values shown in the grid must match the list endpoint's
    // global ranking context. Recompute by refetching the sorted list instead
    // of patching a single card from metadata.
    preserveScrollOnNextFetch.value = true;
    lastFetchSuccess.value = { key: "", at: 0 };
    lastFetchError.value = { key: "", at: 0 };
    await fetchAllGridImages({ force: true });
    updateVisibleThumbnails();
  } else {
    refreshGridImage(imageId);
  }
}

async function addTagToImage(imageId, tag) {
  try {
    const response = await addPictureTag(imageId, tag);
    const responseTags = getTagList(response?.tags);
    const gridImg = allGridImages.value.find(
      (img) => img && img.id === imageId,
    );
    if (gridImg) {
      const current = getTagList(gridImg.tags);
      const merged = responseTags.length
        ? responseTags
        : dedupeTagList([...current, { id: null, tag }]);
      gridImg.tags = merged;
    }
    await _afterTagMutation(imageId);
  } catch (error) {
    console.error("Error adding tag:", error);
  }
}

function updateDescriptionForImage(imageId, description) {
  const gridImg = allGridImages.value.find((img) => img && img.id === imageId);
  if (gridImg) {
    gridImg.description = description;
  }
  refreshGridImage(imageId);
}

// ============================================================
// LIFECYCLE
// ============================================================

watch(
  () => gridStore.thumbnailSize,
  () => {
    // Recalculate visibleStart and visibleEnd after rowHeight update
    nextTick(() => {
      updateRowHeightFromGrid();
      if (isJustifiedMode.value) {
        // Justified rows don't follow the uniform cols/rowHeight arithmetic
        // below - let the virtualizer derive the range from the packed model.
        recalculateVisibleRange();
        return;
      }
      const el = scrollWrapper.value;
      if (!el) return;
      let cardHeight = rowHeight.value;
      const scrollTop = el.scrollTop;
      const cols = gridStore.columns;
      // First visible row (may be partially visible)
      const firstVisibleRow = scrollTop / cardHeight;
      // Last visible row (may be partially visible)
      const lastVisibleRow = (scrollTop + el.clientHeight - 1) / cardHeight;
      const newVisibleStart = Math.floor(firstVisibleRow) * cols;
      const newVisibleEnd = Math.ceil(lastVisibleRow) * cols;
      visibleStart.value = newVisibleStart;
      visibleEnd.value = newVisibleEnd;
      updateVisibleThumbnails();
    });
  },
);

// Expose the grid DOM node to parent
defineExpose({
  gridEl: scrollWrapper,
  onGlobalKeyPress,
  updateVisibleThumbnails,
  expandAllStacks,
  collapseAllStacks,
  exportCurrentViewToZip,
  exportCurrentViewToFolder,
  getExportCount,
  removeImagesById,
  insertGridImagesById,
  refreshGridImage,
  // The stack badge's own reconcile. Separate from refreshGridImage because
  // `stack_count` is derived per stack and is absent from a card's /metadata
  // read, so the per-card refresh cannot repair it.
  refreshStackFacets,
  // Same shape, different value: a card's thumbnail URL comes from the batch
  // thumbnail endpoint and is absent from /metadata, so refreshGridImage alone
  // cannot repair a tile whose FILE changed (an in-place rotate, or an undo of
  // one arriving over the socket).
  refreshThumbnailUrls,
  // A bytes change (an in-place rotate, or an undo/redo of one over the socket)
  // moves the tile's shape and its bitmap from two different reads. This lands
  // them as one visual change; nothing that reacts to `pixels` should be doing
  // the two refreshes by hand.
  applyRotatedCards,
  repositionImageByScore,
  repositionImageBySmartScore,
  refreshSmartScoreForImage,
  isImagesLoading: () => imagesLoading.value,
  isOverlayOpen: () => overlayOpen.value,
  markOverlayDeferredRefresh,
  clearFaceSelection,
  runComfyuiOnGridImages,
  hasCursorFocus: computed(() => cursorIdx.value !== null),
  // Lets the sidebar's Scrapheap context menu reach the same consent-gated
  // empty-scrapheap flow the empty-state placeholder uses. The caller navigates
  // to the scrapheap view first, so the post-confirm grid refetch is correct.
  // It arrives mid-fetch by construction, which is why the confirm gates on a
  // purge already running rather than on the grid's load state.
  confirmEmptyScrapheap,
  // Lets the sidebar's person context menu arm the character-scoped face search
  // ("Suggest more pictures of <person>", #636). Same Tier-3 route as
  // confirmEmptyScrapheap above: sidebar → App.vue → this grid.
  suggestPicturesForCharacter: handleSuggestPicturesForCharacter,
});

// Queue a deferred in-place grid reconcile to run when the overlay closes.
// Used by the realtime-sync decision table: while the overlay is open we never
// raise a pill or reshuffle the grid under the frozen filmstrip; instead we
// flag the refetch so closeOverlay() applies the filter-removal / re-sort
// directly (see closeOverlay's pendingOverlayGridRefresh branch).
function markOverlayDeferredRefresh() {
  if (!overlayOpen.value) return;
  pendingOverlayGridRefresh.value = true;
}

// ============================================================
// SCRAPHEAP GHOST TILES
// ============================================================
// A move to the Scrapheap does not take its thumbnails away. The tiles stay
// exactly where they are, ghosted, for as long as the undo is one click away
// (the receipt's destructive dwell, hover-freeze and hidden-tab pause included);
// only when that window closes does the grid close the gap. Undo inside the
// window un-ghosts them in place, with no refetch and no flash.
//
// The state machine and its clock live in `useOperationStore` - deliberately,
// because the clock IS the receipt's. Everything here is the grid's half:
// which tiles carry the flag, and the imperative collapse when the store hands
// the ids back.
//
// The virtualisation rule is untouched: ghosting FLAGS items, it never
// restructures `allGridImages` or `lastFetchedGridImages`. The only array
// mutation is the collapse, which goes through `removeImagesById` exactly as a
// plain delete always did.

const ghostedIdSet = computed(() => {
  // Not in the Scrapheap view. There these pictures are not on their way out,
  // they have arrived - a ghosted tile would mean the opposite of what it means
  // in the grid, and the view already renders the real auto-purge countdown.
  if (isScrapheapView.value) return null;
  const ids = operationStore.ghostPictureIds;
  if (!ids || !ids.length) return null;
  return new Set(ids.map((id) => String(getPictureId(id))));
});

function isImageGhosted(img) {
  const set = ghostedIdSet.value;
  if (!set) return false;
  const pictureId = getPictureId(img?.id ?? img);
  return pictureId !== null && set.has(String(pictureId));
}

/** Index-based form, for the keyboard cursor's skip scan. */
function isGridIndexGhosted(index) {
  const set = ghostedIdSet.value;
  if (!set) return false;
  const img = allGridImages.value?.[index];
  const pictureId = getPictureId(img?.id);
  return pictureId !== null && set.has(String(pictureId));
}

// A ghost is never in the selection and never under the cursor: every bulk
// action reads the selection, and arming one against pictures that are already
// in the Scrapheap is the trap this whole state exists to avoid.
watch(ghostedIdSet, (set) => {
  if (!set || !set.size) return;
  const isGhostId = (id) => set.has(String(getPictureId(id)));
  if (selectedImageIds.value.some(isGhostId)) {
    selectedImageIds.value = selectedImageIds.value.filter(
      (id) => !isGhostId(id),
    );
  }
  if (
    lastSelectedImageId.value != null &&
    isGhostId(lastSelectedImageId.value)
  ) {
    lastSelectedImageId.value = null;
  }
  if (cursorIdx.value !== null && isGridIndexGhosted(cursorIdx.value)) {
    cursorIdx.value = null;
  }
});

// Any full refetch rebuilds the grid without the scrapheaped pictures, so there
// is nothing left to grey out. Forget the set SILENTLY - no collapse, and the
// receipt is untouched, because undo is still on offer, it just has no tiles to
// put back in this view any more.
watch(allGridImages, () => {
  if (operationStore.ghostState !== GHOST_PENDING) return;
  const present = new Set(
    (allGridImages.value || []).map((img) => String(getPictureId(img?.id))),
  );
  const anyGone = operationStore.ghostPictureIds.some(
    (id) => !present.has(String(getPictureId(id))),
  );
  if (anyGone) operationStore.dropGhosts();
});

// The store hands back the ids whose window has closed.
watch(
  () => operationStore.collapsingPictureIds,
  (ids) => {
    if (!ids || !ids.length) return;
    const doomed = operationStore.takeCollapsingGhosts();
    if (doomed.length) void collapseGhostedImages(doomed);
  },
);

/** Pixel top of a grid item, in whichever layout mode is active. */
function gridItemTopOffset(index) {
  if (index == null || index < 0) return null;
  if (isJustifiedMode.value) {
    const layout = justifiedLayout.value;
    if (!layout || !layout.rowOffsets?.length) return null;
    const row = rowOfIndex(layout.rowStarts, index);
    if (row == null || row < 0) return null;
    const top = layout.rowOffsets[row];
    return typeof top === "number" ? top : null;
  }
  const cols = Math.max(1, gridStore.columns || 1);
  return Math.floor(index / cols) * rowHeight.value;
}

/**
 * Drop the ghosted tiles and close the gap, without yanking the viewport.
 *
 * The collapse is on a timer, which is the one thing the grid's own rules never
 * do (the pills exist so nothing reshuffles under the user unprompted). What
 * makes it acceptable is that content the user is actually looking at does not
 * move: anchor on the topmost item still on screen, remove, then put that item
 * back under the same pixel. Ghosts below the fold move nothing; ghosts on
 * screen close their gap in plain sight, which is the expected consequence of
 * the sentence the receipt just read out; ghosts scrolled off the top would
 * otherwise drag the whole view up under someone who has moved on.
 */
async function collapseGhostedImages(ids) {
  const wrapper = scrollWrapper.value;
  const scrollTop = wrapper ? wrapper.scrollTop : 0;
  let anchorId = null;
  let anchorOffset = 0;
  if (wrapper && scrollTop > 0) {
    const doomed = new Set(
      ids.map((id) => String(getPictureId(id))).filter((id) => id !== "null"),
    );
    const list = allGridImages.value || [];
    for (let i = 0; i < list.length; i += 1) {
      const pictureId = getPictureId(list[i]?.id);
      if (pictureId === null || doomed.has(String(pictureId))) continue;
      const top = gridItemTopOffset(i);
      if (top === null) break;
      if (top >= scrollTop) {
        anchorId = pictureId;
        anchorOffset = top - scrollTop;
        break;
      }
    }
  }

  removeImagesById(ids);

  if (anchorId === null || !wrapper) return;
  await nextTick();
  const newIndex = (allGridImages.value || []).findIndex(
    (img) => getPictureId(img?.id) === anchorId,
  );
  if (newIndex < 0) return;
  const newTop = gridItemTopOffset(newIndex);
  if (newTop === null) return;
  const target = Math.max(0, newTop - anchorOffset);
  // Sub-pixel churn is not worth a scroll write (and would fight momentum).
  if (Math.abs(target - wrapper.scrollTop) > 1) {
    wrapper.scrollTop = target;
  }
}

// Remove images by ID (for event-driven removal)
function removeImagesById(imageIds) {
  if (!Array.isArray(imageIds) || !imageIds.length) {
    return;
  }
  const dIds = new Set(
    imageIds.map((id) => getPictureId(id)).filter((id) => id !== null),
  );
  const removeId = (img) => dIds.has(getPictureId(img?.id));
  allGridImages.value = allGridImages.value.filter((img) => !removeId(img));
  if (Array.isArray(lastFetchedGridImages.value)) {
    lastFetchedGridImages.value = lastFetchedGridImages.value.filter(
      (img) => !removeId(img),
    );
  }
  const nextMembers = new Map();
  for (const [stackId, entry] of expandedStackMembers.value.entries()) {
    const ids = Array.isArray(entry?.ids) ? entry.ids : [];
    const images = Array.isArray(entry?.images) ? entry.images : [];
    const nextIds = ids.filter((id) => !dIds.has(getPictureId(id)));
    const nextImages = images.filter((img) => !removeId(img));
    if (nextIds.length || nextImages.length) {
      nextMembers.set(stackId, { ids: nextIds, images: nextImages });
    }
  }
  expandedStackMembers.value = nextMembers;
  selectedImageIds.value = selectedImageIds.value.filter(
    (id) => !dIds.has(getPictureId(id)),
  );
  resetThumbnailState();
  rebuildGridImagesFromLastFetch();
  void refreshExpandedStacksAfterFetch();
}

function getExportCount() {
  const selectedCount = selectedImageIds.value.length;
  const totalCount = allGridImages.value.filter((img) => img && img.id).length;
  return { selectedCount, totalCount };
}

// ============================================================
// EXPORT
// ============================================================
async function exportCurrentViewToZip(options = {}) {
  const exportType = options.exportType || "full";
  const captionMode = options.captionMode || "description";
  const tagFormat = options.tagFormat || "spaces";
  const includeCharacterName = options.includeCharacterName !== false;
  const useOriginalFileNames = options.useOriginalFileNames === true;
  const resolution = options.resolution || "original";
  const bboxMode = options.bboxMode || "none";
  let params;
  const selectedIds = selectedImageIds.value;
  if (selectedIds && selectedIds.length > 0) {
    const selParams = new URLSearchParams();
    for (const id of selectedIds) {
      selParams.append("id", getPictureId(id));
    }
    params = selParams.toString();
  } else {
    params = buildPictureIdsQueryParams();
  }
  const extraParams = new URLSearchParams();
  if (exportType) {
    extraParams.append("export_type", exportType);
  }
  if (captionMode) {
    extraParams.append("caption_mode", captionMode);
  }
  if (captionMode === "tags" && tagFormat === "underscores") {
    extraParams.append("tag_format", "underscores");
  }
  if (includeCharacterName) {
    extraParams.append("include_character_name", "true");
  }
  if (useOriginalFileNames) {
    extraParams.append("use_original_file_names", "true");
  }
  if (resolution) {
    extraParams.append("resolution", resolution);
  }
  if (bboxMode && bboxMode !== "none") {
    extraParams.append("bbox_mode", bboxMode);
  }
  const extraParamString = extraParams.toString();
  const exportQuery = [params, extraParamString].filter(Boolean).join("&");

  try {
    exportProgress.visible = true;
    exportProgress.status = "starting";
    exportProgress.processed = 0;
    exportProgress.total = 0;
    exportProgress.message = "Preparing export...";
    exportProgress.cancelRequested = false;

    const startBody = await startExport(exportQuery);
    const taskId = startBody?.task_id;
    if (!taskId) {
      throw new Error("Missing task_id from export response.");
    }

    let downloadUrl = null;
    const maxAttempts = 600; // 600 × 1s = 10 minute timeout; suitable for very large collections
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      if (exportProgress.cancelRequested) {
        exportProgress.status = "cancelled";
        exportProgress.message = "Export cancelled.";
        exportProgress.visible = false;
        return;
      }
      const statusBody = await getExportStatus(taskId);
      const status = statusBody?.status;
      exportProgress.status = status || "in_progress";
      exportProgress.processed = statusBody?.processed || 0;
      exportProgress.total = statusBody?.total || 0;
      exportProgress.message =
        status === "completed"
          ? "Finalizing download..."
          : "Exporting images...";
      if (status === "completed") {
        downloadUrl = statusBody?.download_url;
        break;
      }
      if (status === "failed") {
        throw new Error("Export failed on server.");
      }
      await sleep(1000);
    }

    if (exportProgress.cancelRequested) {
      exportProgress.status = "cancelled";
      exportProgress.message = "Export cancelled.";
      exportProgress.visible = false;
      return;
    }

    if (!downloadUrl) {
      throw new Error("Export timed out waiting for ZIP.");
    }

    const { blob, filename } = await downloadExport(downloadUrl);

    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    setTimeout(() => {
      URL.revokeObjectURL(link.href);
      document.body.removeChild(link);
      exportProgress.visible = false;
      exportProgress.status = "idle";
      exportProgress.message = "";
    }, 2000);
  } catch (e) {
    exportProgress.status = "failed";
    exportProgress.message = "Export failed";
    console.error("ZIP export failed", e);
    noticeStore.error(`Export failed. ${errorDetail(e)}`, { key: "export-zip",
    });
    setTimeout(() => {
      exportProgress.visible = false;
      exportProgress.status = "idle";
      exportProgress.message = "";
    }, 4000);
  }
}

function abortExport() {
  if (!exportProgress.visible) return;
  exportProgress.cancelRequested = true;
}

// Local-owner counterpart to exportCurrentViewToZip (#291): writes straight
// into a folder on this machine instead of packaging a ZIP to download, then
// the server opens that folder in the host file manager once done.
async function exportCurrentViewToFolder(options = {}) {
  const destination = options.destination;
  if (!destination) return;
  const exportType = options.exportType || "full";
  const captionMode = options.captionMode || "description";
  const tagFormat = options.tagFormat || "spaces";
  const includeCharacterName = options.includeCharacterName !== false;
  const useOriginalFileNames = options.useOriginalFileNames === true;
  const resolution = options.resolution || "original";
  const bboxMode = options.bboxMode || "none";
  let params;
  const selectedIds = selectedImageIds.value;
  if (selectedIds && selectedIds.length > 0) {
    const selParams = new URLSearchParams();
    for (const id of selectedIds) {
      selParams.append("id", getPictureId(id));
    }
    params = selParams.toString();
  } else {
    params = buildPictureIdsQueryParams();
  }
  const extraParams = new URLSearchParams();
  extraParams.append("destination", destination);
  if (exportType) {
    extraParams.append("export_type", exportType);
  }
  if (captionMode) {
    extraParams.append("caption_mode", captionMode);
  }
  if (captionMode === "tags" && tagFormat === "underscores") {
    extraParams.append("tag_format", "underscores");
  }
  if (includeCharacterName) {
    extraParams.append("include_character_name", "true");
  }
  if (useOriginalFileNames) {
    extraParams.append("use_original_file_names", "true");
  }
  if (resolution) {
    extraParams.append("resolution", resolution);
  }
  if (bboxMode && bboxMode !== "none") {
    extraParams.append("bbox_mode", bboxMode);
  }
  const extraParamString = extraParams.toString();
  const exportQuery = [params, extraParamString].filter(Boolean).join("&");

  try {
    exportProgress.visible = true;
    exportProgress.status = "starting";
    exportProgress.processed = 0;
    exportProgress.total = 0;
    exportProgress.message = "Preparing export...";
    exportProgress.cancelRequested = false;

    const startBody = await startFolderExport(exportQuery);
    const taskId = startBody?.task_id;
    if (!taskId) {
      throw new Error("Missing task_id from export response.");
    }

    let completedBody = null;
    const maxAttempts = 600; // 600 × 1s = 10 minute timeout; suitable for very large collections
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      if (exportProgress.cancelRequested) {
        exportProgress.status = "cancelled";
        exportProgress.message = "Export cancelled.";
        exportProgress.visible = false;
        return;
      }
      const statusBody = await getExportStatus(taskId);
      const status = statusBody?.status;
      exportProgress.status = status || "in_progress";
      exportProgress.processed = statusBody?.processed || 0;
      exportProgress.total = statusBody?.total || 0;
      exportProgress.message =
        status === "completed" ? "Opening folder..." : "Exporting images...";
      if (status === "completed") {
        completedBody = statusBody;
        break;
      }
      if (status === "failed") {
        throw new Error("Export failed on server.");
      }
      await sleep(1000);
    }

    if (exportProgress.cancelRequested) {
      exportProgress.status = "cancelled";
      exportProgress.message = "Export cancelled.";
      exportProgress.visible = false;
      return;
    }

    if (!completedBody) {
      throw new Error("Export timed out.");
    }

    const count = exportProgress.processed;
    // The server resolves the destination (realpath) before writing to it and
    // opening it, so what it actually used can differ from the raw string the
    // picker returned (a symlink, a trailing slash, a `..`) - show that one.
    const resolvedDestination = completedBody.destination || destination;
    if (completedBody.opened === false) {
      noticeStore.warning(
        `Exported ${count} picture${count === 1 ? "" : "s"} to ${resolvedDestination}, ` +
          `but couldn't open it - no desktop file manager found on the machine running PixlStash.`,
        { key: "export-folder" },
      );
    } else {
      noticeStore.success(
        `Exported ${count} picture${count === 1 ? "" : "s"} to ${resolvedDestination}.`,
        { key: "export-folder" },
      );
    }
    setTimeout(() => {
      exportProgress.visible = false;
      exportProgress.status = "idle";
      exportProgress.message = "";
    }, 2000);
  } catch (e) {
    exportProgress.status = "failed";
    exportProgress.message = "Export failed";
    console.error("Folder export failed", e);
    const status = e?.response?.status;
    const message =
      status === 403
        ? "Exporting to a folder only works from the machine running PixlStash itself."
        : status === 404
          ? "That folder isn't available on the machine running PixlStash. Pick a different destination."
          : status === 409
            ? "That folder isn't empty. Pick or create an empty folder to export into."
            : `Export failed. ${errorDetail(e)}`;
    noticeStore.error(message, { key: "export-folder" });
    setTimeout(() => {
      exportProgress.visible = false;
      exportProgress.status = "idle";
      exportProgress.message = "";
    }, 4000);
  }
}

// ============================================================
// SEARCH
// ============================================================
/** Drop every non-text search mode. Called before arming a new one. */
function resetFaceAndImageSearches() {
  reverseImageSearchPictureIds.value = [];
  faceLikenessSearchFaceId.value = null;
  clearCharacterFaceSearch();
}

/** Forget the character search, its cached ranked list and both its knobs. */
function clearCharacterFaceSearch() {
  faceSearchCharacter.value = null;
  faceSearchRanked.value = null;
  faceSearchThreshold.value = FACE_SEARCH_DEFAULT_THRESHOLD;
  faceSearchMinRefs.value = 1;
  faceSearchArmedView.value = null;
}

function clearSearchQuery() {
  resetFaceAndImageSearches();
  emit("clear-search", "");
}

function handleReverseImageSearch() {
  const ids = selectedImageIds.value?.length
    ? selectedImageIds.value.slice()
    : [];
  if (!ids.length) {
    // Fallback: just the right-clicked image
    const imgId = contextMenuImage.value?.id;
    if (!imgId) return;
    ids.push(imgId);
  }
  faceLikenessSearchFaceId.value = null;
  clearCharacterFaceSearch();
  reverseImageSearchPictureIds.value = ids;
  // Clear any active text search so the two modes don't overlap.
  emit("clear-search", "");
}

function handleFindSimilarFaces(faceId) {
  if (!faceId) return;
  reverseImageSearchPictureIds.value = [];
  clearCharacterFaceSearch();
  faceLikenessSearchFaceId.value = faceId;
  emit("clear-search", "");
}

/**
 * Move the suggestion threshold.
 *
 * The ref is set synchronously so the count in the bar tracks the drag, while
 * the grid rebuild is debounced: it costs no network call (the ranked list and
 * its rows are cached) but it does re-render the virtual grid, and doing that on
 * every pointer sample would stutter. 200ms is under the ~250ms at which a
 * response stops reading as immediate.
 *
 * @param {number} value - the new cut, 0-1.
 */
function handleFaceSearchThreshold(value) {
  const next = Number(value);
  if (!Number.isFinite(next)) return;
  faceSearchThreshold.value = next;
  debouncedFaceSearchRecut();
}

/**
 * Move the reference-agreement floor: how many of the person's reference faces
 * must clear the strength cut.
 *
 * Same shape as the threshold above, and for the same reason: it re-cuts the
 * cached ranked list, so it costs no network call and must not stutter the grid.
 * Clamped against the reference count because that count only arrives with the
 * ranked list, so a stale higher value would otherwise empty the results.
 *
 * @param {number} value - references that must agree, 1..N.
 */
function handleFaceSearchMinRefs(value) {
  const next = Math.round(Number(value));
  if (!Number.isFinite(next)) return;
  const ceiling = Math.max(1, faceSearchRefCount.value || 1);
  faceSearchMinRefs.value = Math.min(ceiling, Math.max(1, next));
  debouncedFaceSearchRecut();
}

const debouncedFaceSearchRecut = debounce(() => {
  if (!faceSearchCharacter.value) return;
  fetchAllGridImages({ force: false }).then(() => updateVisibleThumbnails());
}, 200);

/**
 * Arm "Suggest more pictures of <person>" from the sidebar's person menu (#636).
 *
 * @param {{id: number|string, name: string}} character
 */
function handleSuggestPicturesForCharacter(character) {
  const id = character?.id;
  if (id == null) return;
  reverseImageSearchPictureIds.value = [];
  faceLikenessSearchFaceId.value = null;
  faceSearchRanked.value = null;
  faceSearchThreshold.value = FACE_SEARCH_DEFAULT_THRESHOLD;
  faceSearchMinRefs.value = 1;
  faceSearchCharacter.value = { id, name: character.name ?? "this person" };
  // Snapshot the view this was armed from. Opening the person's context menu can
  // itself select that person, so "the view changed" has to mean "changed from
  // where the search started", not "a selection watcher fired". Otherwise the
  // clear below cancels the search the click just asked for.
  faceSearchArmedView.value = {
    character: selectionStore.selectedCharacter,
    set: selectionStore.selectedSet,
  };
  emit("clear-search", "");
  // The clear-search emit bumps gridVersion, but that watcher throttles itself
  // to one refresh per 1200ms - and this search is the direct result of a click,
  // so it must not be the one that gets dropped. Fetching here as well is safe:
  // the two calls share a fetch key and the second de-dups against the first.
  nextTick(() => {
    void (async () => {
      const outcome = await fetchAllGridImages({ force: true });
      if (
        faceSearchCharacter.value?.id === id &&
        outcome?.error?.gridFetchPhase === "character-face-search-request"
      ) {
        const failure = outcome.error;
        clearCharacterFaceSearch();
        await fetchAllGridImages({ force: true });
        noticeStore.error(
          `Couldn't load suggestions for ${character.name ?? "this person"}. ${errorDetail(failure)}`,
          { key: "character-face-search-load" },
        );
        return;
      }
      updateVisibleThumbnails();
    })();
  });
}

// Every match surviving both knobs. Computed from the cached ranked list rather
// than from `allGridImages`, so the count in the bar tracks the sliders
// immediately while the grid rebuild debounces behind it. Shares its cut with
// the rebuild (`utils/faceSuggestionCut.js`) so the two cannot drift.
const faceSearchMatches = computed(() => {
  const cached = faceSearchRanked.value;
  if (!cached || !faceSearchCharacter.value) return [];
  if (cached.characterId !== faceSearchCharacter.value.id) return [];
  return cutFaceSuggestions(
    cached.matches,
    faceSearchThreshold.value ?? 0,
    faceSearchMinRefs.value,
  );
});

// How many reference faces the query carried. 0 when the ranked list is not in
// yet or the server did not send the per-reference rows. The agreement slider
// then has nothing to offer and the panel drops it rather than show a control
// whose only position is its minimum.
const faceSearchRefCount = computed(() => {
  const cached = faceSearchRanked.value;
  if (!cached || !faceSearchCharacter.value) return 0;
  if (cached.characterId !== faceSearchCharacter.value.id) return 0;
  return referenceFaceCount(cached.matches);
});

// Selection wins over the threshold: a button that ignored an explicit
// selection of twelve to write forty-one would be the error, not the shortcut.
const faceSearchAssignFromSelection = computed(
  () => selectedImageIds.value.length > 0,
);

const faceSearchAssignIds = computed(() =>
  faceSearchAssignFromSelection.value
    ? selectedImageIds.value.slice()
    : faceSearchMatches.value.map((m) => m.picture_id),
);

/**
 * Assign the suggested (or selected) pictures to the searched person.
 *
 * Sends the exact picture/face pairs the search returned. A suggestion is an
 * explicit reviewed winner; rescoring it during assignment can attach a
 * different face if detections or references changed in between.
 */
async function handleAssignFaceSearchResults() {
  const character = faceSearchCharacter.value;
  const ids = faceSearchAssignIds.value;
  if (!character || !ids.length || faceSearchAssignBusy.value) return;
  const wanted = new Set(ids.map(String));
  const byPicture = new Map();
  for (const match of faceSearchMatches.value) {
    if (!wanted.has(String(match?.picture_id))) continue;
    if (match?.picture_id == null || match?.face_id == null) continue;
    byPicture.set(String(match.picture_id), {
      picture_id: match.picture_id,
      face_id: match.face_id,
    });
  }
  const assignments = [...byPicture.values()];
  if (assignments.length !== wanted.size) {
    noticeStore.warning(
      "Some selected suggestions no longer have a reviewed face match. Refresh suggestions before assigning.",
      { key: "character-face-search-stale-selection" },
    );
    return;
  }
  faceSearchAssignBusy.value = true;
  try {
    await addCharacterFaceAssignments(character.id, assignments);
    selectedImageIds.value = [];
    clearFaceSelection();
    lastSelectedImageId.value = null;
    // Re-run the search against the server rather than pruning the cached list
    // locally. Two reasons, both correctness: the assignment is stack-atomic, so
    // it can have assigned MORE pictures than were named here (a suggestion's
    // stack siblings would otherwise stay on screen as un-assigned), and the
    // fetch key has not changed, so a non-forced call would be dropped by the
    // de-dup window and leave the grid showing what was just assigned.
    faceSearchRanked.value = null;
    await fetchAllGridImages({ force: true });
    // Once the refreshed cut has no suggestions left, the temporary search
    // has served its purpose. Drop it and reload the still-selected view that
    // was underneath it instead of leaving the user in an empty suggestion
    // grid. If any matches remain, keep the mode open for another assignment.
    if (faceSearchMatches.value.length === 0) {
      clearCharacterFaceSearch();
      await fetchAllGridImages({ force: true });
    }
    updateVisibleThumbnails();
    // Raises the "Assigned N pictures…· Undo" receipt for this client.
    operationStore.refresh();
    emit("refresh-sidebar");
  } catch (e) {
    const status = Number(e?.response?.status);
    if (status >= 500 || status === 422) {
      faceSearchRanked.value = null;
      await fetchAllGridImages({ force: true });
    }
    if (status >= 500) {
      noticeStore.error(
        `The assignment outcome is uncertain. Suggestions were reloaded from the server before you retry.`,
        { key: "character-face-search-assign-uncertain" },
      );
      return;
    }
    noticeStore.error(
      `Couldn't assign those pictures to ${character.name}. ${errorDetail(e)}`,
      { scope: "character-face-search-assign" },
    );
  } finally {
    faceSearchAssignBusy.value = false;
  }
}

// Clear reverse image search / face search when the user starts a text search or navigates.
watch(
  () => searchStore.searchQuery,
  (newVal) => {
    if (newVal && newVal.trim()) {
      resetFaceAndImageSearches();
    }
  },
);
/**
 * Drop every non-text search mode because the user navigated somewhere else.
 *
 * **Called from the view-change watcher BEFORE it refetches, never from a
 * watcher of its own.** `fetchAllGridImages` reads the search-mode refs
 * synchronously (there is no `await` before it picks its `fetchMode`), and Vue
 * runs pre-flush watchers in creation order, so a separate clearing watcher
 * declared after the fetching one always loses: the fetch captures the search
 * that is still armed and repopulates the grid with it, then the clear runs and
 * unmounts the pill. The result is a grid that keeps showing the old search
 * with no bar to explain or dismiss it, and a view that never changes.
 *
 * @param {string|number|null} character - the newly selected character.
 * @param {string|number|null} set - the newly selected set.
 */
function dropSearchesForViewChange(character, set) {
  if (reverseImageSearchPictureIds.value?.length) {
    reverseImageSearchPictureIds.value = [];
  }
  if (faceLikenessSearchFaceId.value !== null) {
    faceLikenessSearchFaceId.value = null;
  }
  // The character search goes too. It is library-wide, so a view change cannot
  // invalidate its *results*, but leaving it up meant navigating somewhere else
  // and still being shown a grid of suggestions for a person, with a bulk
  // Assign button armed, which reads as the new view's contents. Navigation is
  // the ordinary way out of a mode; making it the exit here as well costs one
  // re-arm and removes a standing bulk write from every view the user visits.
  //
  // Compared against the armed-from view rather than fired on any change:
  // opening the sidebar's person menu can itself select that person, and that
  // selection lands around the same click that arms the search.
  if (!faceSearchCharacter.value) return;
  const armed = faceSearchArmedView.value;
  if (armed && armed.character === character && armed.set === set) return;
  clearCharacterFaceSearch();
}

function handleEmptyStateReset() {
  gridReady.value = false;
  emptyStateDelayPassed.value = false;
  resetFaceAndImageSearches();
  emit("reset-to-all");
}
</script>
<style scoped src="./ImageGrid.css"></style>
<style src="./ImageGrid.global.css"></style>
