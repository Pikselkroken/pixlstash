<template>
  <ImageOverlay
    :open="overlayOpen"
    :initialImageId="overlayImageId"
    :initialExpandedStackIds="overlayInitialExpandedStackIds"
    :allImages="allGridImages"
    :backendUrl="props.backendUrl"
    :tagUpdate="props.wsTagUpdate"
    :hiddenTags="props.hiddenTags"
    :applyTagFilter="props.applyTagFilter"
    :dateFormat="props.dateFormat"
    :showStacks="props.showStacks"
    :showProblemIcon="props.showProblemIcon"
    :availablePlugins="availablePlugins"
    :comfyuiProgress="comfyuiProgress"
    :comfyuiProgressPercent="comfyuiProgressPercent"
    :pluginProgress="pluginProgress"
    :pluginProgressPercent="pluginProgressPercent"
    :comfyuiClientId="comfyuiClientId"
    :comfyuiConfigured="props.comfyuiConfigured"
    :guestScore="overlayGuestScore"
    @close="closeOverlay"
    @apply-score="applyScore"
    @set-guest-score="(img, n) => setGuestScore(img, n)"
    @add-tag="addTagToImage"
    @remove-tag="removeTagFromImage"
    @update-description="updateDescriptionForImage"
    @overlay-change="handleOverlayChange"
    @added-to-set="handleOverlayAddedToSet"
    @set-project="handleSetProjectForSelected"
    @comfyui-run="handleComfyuiRun"
    @run-plugin="handlePluginRunRequest"
  />
  <ImageImporter
    ref="imageImporterRef"
    :backendUrl="props.backendUrl"
    :selectedCharacterId="props.selectedCharacter"
    :allPicturesId="props.allPicturesId"
    :unassignedPicturesId="props.unassignedPicturesId"
    @import-started="handleImportStarted"
    @import-finished="handleImagesUploaded"
    @import-cancelled="handleImportCancelled"
    @import-error="handleImportErrored"
  />
  <div :style="wrapperStyle" class="grid-content-area">
    <Toolbar
      ref="selectionBarRef"
      :selectedCount="selectedImageIds.length"
      :selectedExpandedCount="selectedExpandedCount"
      :selectedFaceCount="selectedFaceIds.length"
      :selectedCharacter="String(props.selectedCharacter)"
      :selectedSet="String(props.selectedSet)"
      :selectedGroupName="selectedGroupName"
      :selectedSort="props.selectedSort"
      :allPicturesId="String(props.allPicturesId)"
      :unassignedPicturesId="String(props.unassignedPicturesId)"
      :scrapheapPicturesId="String(props.scrapheapPicturesId)"
      :backend-url="props.backendUrl"
      :selected-image-ids="selectedImageIds"
      :selected-media-support="selectedMediaSupport"
      :comfyui-client-id="comfyuiClientId"
      :comfyui-configured="props.comfyuiConfigured"
      :available-plugins="availablePlugins"
      :show-remove-from-stack="showRemoveFromStack"
      :selected-multiple-stack-ids="selectedMultipleStackIds"
      :all-grid-images="allGridImages"
      :visible="showSelectionBar"
      @clear-selection="clearSelection"
      @added-to-set="handleOverlayAddedToSet"
      @remove-from-group="removeFromGroup"
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
      @comfyui-run-grid="runComfyuiOnGridImages"
      @tags-applied="fetchAllGridImages({ force: true, showProgress: true })"
      @expand-all-stacks="expandAllStacks"
      @collapse-all-stacks="collapseAllStacks"
      @open-settings="emit('open-settings')"
      @open-import="emit('open-import')"
      @confirm-export-zip="emit('confirm-export-zip')"
    />
    <!-- ── Visible range pill ── -->
    <transition name="grid-range-fade">
      <span v-if="visibleRangeLabel" class="grid-range-pill">{{
        visibleRangeLabel
      }}</span>
    </transition>
    <!-- ── Streaming "loading more" pill ── -->
    <transition name="grid-range-fade">
      <span
        v-if="imagesLoading && allGridImages.length > 0"
        class="grid-loading-more-pill"
        >Loading more…</span
      >
    </transition>
    <ImageGridContextMenu
      :visible="contextMenuVisible"
      :x="contextMenuX"
      :y="contextMenuY"
      :selected-image-ids="selectedImageIds"
      :selected-media-support="selectedMediaSupport"
      :selected-character="String(props.selectedCharacter)"
      :selected-set="String(props.selectedSet)"
      :selected-group-name="selectedGroupName"
      :selected-sort="props.selectedSort"
      :all-pictures-id="String(props.allPicturesId)"
      :unassigned-pictures-id="String(props.unassignedPicturesId)"
      :scrapheap-pictures-id="String(props.scrapheapPicturesId)"
      :backend-url="props.backendUrl"
      :comfyui-configured="props.comfyuiConfigured"
      :show-remove-from-stack="showRemoveFromStack"
      :selected-multiple-stack-ids="selectedMultipleStackIds"
      :available-plugins="availablePlugins"
      :context-image="contextMenuImage"
      :is-shared="
        contextMenuImage ? sharedPictureIds.has(contextMenuImage.id) : false
      "
      @close="contextMenuVisible = false"
      @added-to-set="handleOverlayAddedToSet"
      @add-to-character="handleAddToCharacter"
      @remove-from-character="handleRemoveFromCharacter"
      @set-project="handleSetProjectForSelected"
      @remove-from-stack="removeSelectedFromStack"
      @dissolve-stacks="dissolveSelectedStacks"
      @create-stack="createStackFromSelection"
      @create-stacks-from-groups="createStacksFromSelectedGroups"
      @remove-from-group="removeFromGroup"
      @delete-selected="deleteSelected"
      @open-tag-panel="handleContextMenuOpenTagPanel"
      @open-plugin-panel="handleContextMenuOpenPluginPanel"
      @open-comfyui-panel="handleContextMenuOpenComfyuiPanel"
      @share-picture="sharePicture"
      @remove-picture-shares="openRevokeSharesDialog"
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

    <!-- ── Share picture dialog ──────────────────────────────── -->
    <ShareDialog
      v-model="sharePicDialogOpen"
      resource-type="picture"
      :resource-id="contextMenuImage?.id"
      :resource-format="contextMenuImage?.format"
      :embed-watermark="props.embedWatermark"
      :backend-url="props.backendUrl"
      :public-url="props.publicUrl"
      @update:embed-watermark="emit('update:embed-watermark', $event)"
      @created="onSharePicCreated"
    />
    <EmptyScrapHeap
      v-if="showScrapheapBar"
      :visible="showScrapheapBar"
      :disabled="scrapheapEmptyDisabled"
      :restoreDisabled="scrapheapRestoreDisabled"
      @empty-scrapheap="confirmEmptyScrapheap"
      @restore-scrapheap="confirmRestoreScrapheap"
    />
    <div
      v-if="isMultiCharacterView || isSetOverlapView"
      class="multi-select-toolbar"
    >
      <select
        class="multi-select-toolbar__mode"
        :value="
          isMultiCharacterView ? props.characterMultiMode : props.setMultiMode
        "
        @change="
          (e) =>
            isMultiCharacterView
              ? emit('update:character-multi-mode', e.target.value)
              : emit('update:set-multi-mode', e.target.value)
        "
      >
        <option value="union">Union</option>
        <option value="intersection">Overlap</option>
        <option value="difference">Difference</option>
        <option value="xor">Unique (XOR)</option>
      </select>
      <template
        v-if="!isMultiCharacterView && props.setMultiMode === 'difference'"
      >
        <span class="multi-select-toolbar__separator">|</span>
        <label class="multi-select-toolbar__base-label">Base:</label>
        <select
          class="multi-select-toolbar__base"
          :value="props.setDifferenceBaseId ?? normalizedSelectedSetIds[0]"
          @change="
            (e) => emit('update:set-difference-base-id', Number(e.target.value))
          "
        >
          <option
            v-for="sid in normalizedSelectedSetIds"
            :key="sid"
            :value="sid"
          >
            {{ props.selectedSetNames[sid] || `Set ${sid}` }}
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
      @abort="abortExportZip"
    />
    <ComfyUiRunner
      ref="comfyuiRunner"
      :backendUrl="props.backendUrl"
      :wsPluginProgress="props.wsPluginProgress"
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
      status="running"
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
        v-if="props.pendingExternalImportCount > 0"
        class="pending-imports-pill-anchor"
      >
        <button
          class="pending-imports-pill"
          @click="emit('load-pending-imports')"
        >
          ↑ {{ props.pendingExternalImportCount }}
          {{
            props.pendingExternalImportCount === 1
              ? "new picture"
              : "new pictures"
          }}
          — click to load
        </button>
      </div>
      <div v-if="dragOverlayVisible" class="drag-overlay">
        <div class="drag-overlay-message">{{ dragOverlayMessage }}</div>
      </div>
      <div v-if="showFolderScanningState" class="empty-state">
        <div class="empty-state-card">
          <div class="empty-state-illustration" aria-hidden="true">
            <img
              src="/Empty.png"
              alt="Scanning"
              :style="emptyStateImageStyle"
              style="width: 90%"
            />
          </div>
          <div class="empty-state-title">PixlStash is scanning your folder</div>
          <div class="empty-state-subtitle">Reticulating splines…</div>
        </div>
      </div>
      <div v-if="showEmptyState" class="empty-state">
        <div class="empty-state-card">
          <div class="empty-state-illustration" aria-hidden="true">
            <img
              :src="emptyStateImage"
              :alt="emptyStateAlt"
              :style="emptyStateImageStyle"
              style="width: 90%"
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
            'compact-mode': props.compactMode,
            'touch-select-mode': touchSelectMode,
          },
        ]"
        :style="{
          gridTemplateColumns: `repeat(${props.columns}, minmax(0, ${MAX_THUMBNAIL_SIZE}px))`,
          position: 'relative',
          ...badgeCssVars,
        }"
        ref="gridContainer"
        @click="handleGridBackgroundClick"
      >
        <!-- Top spacer for virtual scroll alignment -->
        <div
          v-if="topSpacerHeight > 0"
          :style="{
            gridColumn: '1 / -1',
            height: `${topSpacerHeight}px`,
          }"
        ></div>
        <div
          v-for="(img, idx) in gridImagesToRender"
          :key="img.id ? `img-${img.id}-${img.idx}` : `placeholder-${img.idx}`"
          :style="getStackCardStyle(img)"
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
            },
          ]"
          @click="handleImageCardClick(img, img.idx, $event)"
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
            @click.stop="handleThumbnailClick(img, img.idx, $event)"
          >
            <div
              :class="[
                'thumbnail-container',
                { 'thumbnail-container-drag-source': isDragSourceImage(img) },
              ]"
              :ref="(el) => setThumbnailContainerRef(img.id, el)"
              draggable="true"
              @dragstart.capture="handleContainerDragStart(img, $event)"
              @dragend.capture="handleDragEnd"
              @dragover="handleStackReorderDragOver(img, $event)"
              @drop="handleStackReorderDrop(img, $event)"
              @dragleave="handleStackReorderDragLeave(img, $event)"
            >
              <!-- Top-left permanent badges (top→bottom): problem, reference folder, share -->
              <div
                v-if="
                  isThumbnailReady(img.id) &&
                  img.thumbnail &&
                  ((props.showProblemIcon && hasPenalisedTags(img)) ||
                    img.reference_folder_id ||
                    (!isReadOnly && sharedPictureIds.has(img.id)))
                "
                class="thumbnail-top-left-badges"
              >
                <div
                  v-if="props.showProblemIcon && hasPenalisedTags(img)"
                  class="penalised-tag-indicator thumbnail-badge"
                  :title="penalisedTagsTitle(img)"
                >
                  <v-icon
                    :size="badgeIconSizes.penalised"
                    :color="penalisedTagColor(img, props.penalisedTagWeights)"
                    >{{
                      penalisedTagIcon(
                        img,
                        props.penalisedTagWeights,
                        props.themeMode !== "light",
                      )
                    }}</v-icon
                  >
                </div>
                <div
                  v-if="img.reference_folder_id"
                  class="thumbnail-reference-badge thumbnail-badge"
                  :title="img.file_path || 'Reference picture'"
                  @click.stop="openReferenceLocation(img.id)"
                >
                  <v-icon :size="badgeIconSizes.penalised">mdi-folder</v-icon>
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
                  :src="getThumbnailSrc(img)"
                  class="thumbnail-img"
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
                  @error="handleImageError"
                  @load="onThumbnailLoad(img.id, $event)"
                />
                <img
                  v-if="isVideo(img)"
                  class="thumbnail-drag-preview"
                  :src="getThumbnailSrc(img)"
                  :ref="(el) => setDragPreviewRef(img.id, el)"
                  alt=""
                />
                <!-- Face bounding box overlays: must be rendered after the image for correct stacking -->
                <template v-if="isThumbnailReady(img.id) && img.thumbnail">
                  <div
                    v-for="overlay in getFaceBboxOverlays(img)"
                    :key="
                      overlay.faceId +
                      '-' +
                      img.id +
                      '-' +
                      (img.thumbnail ? 1 : 0)
                    "
                    class="face-bbox-overlay"
                    :style="overlay.style"
                    draggable="true"
                    @pointerdown.stop
                    @mousedown.stop
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
                  </div>
                </template>
                <div
                  v-if="
                    isThumbnailReady(img.id) &&
                    img.thumbnail &&
                    img.format &&
                    img.format !== 'unknown'
                  "
                  class="thumbnail-bottom-left-badges"
                >
                  <!-- Format badge: hover-only -->
                  <div class="thumbnail-id-overlay thumbnail-badge">
                    {{ img.format.toUpperCase() }}
                  </div>
                </div>
              </template>
              <template v-else>
                <div class="thumbnail-placeholder">
                  <v-icon class="thumbnail-placeholder-icon"
                    >mdi-loading</v-icon
                  >
                </div>
              </template>
              <!-- Stack band overlay (top+bottom color stripe for compact mode) -->
              <div
                v-if="getStackBandStyle(img)"
                class="stack-band-overlay"
                :style="getStackBandStyle(img)"
              ></div>
              <!-- Top-right hover badges: stars + stack indicator -->
              <div
                v-if="isThumbnailReady(img.id) && img.thumbnail"
                class="thumbnail-top-right-badges"
              >
                <StarRatingOverlay
                  v-if="props.showStars"
                  :score="
                    isReadOnly
                      ? (guestScoreMap.get(img.id) ?? img.score ?? 0)
                      : img.score || 0
                  "
                  :icon-size="badgeIconSizes.star"
                  :compact="true"
                  @set-score="setScore(img, $event)"
                />
                <div
                  v-if="shouldShowStackBadge(img)"
                  class="stack-indicator"
                  :title="stackBadgeTitle(img)"
                  @click.stop="toggleStackExpand(img)"
                  @mouseenter.stop="prefetchStackMembers(img)"
                >
                  <v-icon
                    :size="badgeIconSizes.stack"
                    :style="getStackBadgeIconStyle(img)"
                    >mdi-layers-outline</v-icon
                  >
                </div>
              </div>
            </div>
          </div>
          <div v-if="isImageSelected(img.id)" class="selection-overlay"></div>
          <!-- Info row absolutely positioned below thumbnail -->
          <div v-if="!props.compactMode" class="thumbnail-info-row">
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
            height: `${bottomSpacerHeight}px`,
          }"
        ></div>
      </div>
    </div>

    <!-- Search Result Bar -->
    <SearchResultBar
      v-if="props.searchQuery && props.searchQuery.length > 0"
      :images-loading="imagesLoading"
      :count="allGridImages.length"
      :category-label="props.activeCategoryLabel"
      :is-all-pictures-active="props.isAllPicturesActive"
      @search-all="emit('search-all')"
      @clear="clearSearchQuery"
    />

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
import {
  isSupportedImageFile,
  isSupportedVideoFile,
  isVideo,
  getPictureId,
  buildMediaUrl,
} from "../../utils/media.js";
import ImageImporter from "../io/ImageImporter.vue";
import ImageOverlay from "./ImageOverlay.vue";
import EmptyScrapHeap from "../widgets/EmptyScrapHeap.vue";
import Toolbar from "../panels/Toolbar.vue";
import ImageGridContextMenu from "../widgets/ImageGridContextMenu.vue";
import SearchResultBar from "../widgets/SearchResultBar.vue";
import StarRatingOverlay from "../widgets/StarRatingOverlay.vue";
import ComfyUiRunner from "../io/ComfyUiRunner.vue";
import ProgressOverlay from "../widgets/ProgressOverlay.vue";
import ShareDialog from "../io/ShareDialog.vue";
import { apiClient, appendShareToken, isReadOnly } from "../../utils/apiClient";
import {
  arraysEqualByString,
  faceBoxColor,
  formatUserDate,
  getInfoFont,
  getStackColor,
  isRangeOverlap,
  normalizePluginProgressMessage,
  rangeCovers,
  shiftRangesForDelta,
  sleep,
  getStackThreshold,
  toggleScore,
} from "../../utils/utils.js";
import {
  dedupeTagList,
  getTagId,
  hasPenalisedTags,
  penalisedTagsTitle,
  penalisedTagIcon,
  penalisedTagColor,
  getTagList,
  tagMatches,
} from "../../utils/tags.js";
import {
  getStackBadgeCount,
  getPictureStackId,
  selectNewestStackMember,
  shouldShowStackBadge,
  stackBadgeTitle,
} from "../../utils/stack.js";
import { useVirtualScroll } from "../../composables/useVirtualScroll.js";
import { useMultiSelect } from "../../composables/useMultiSelect.js";
import { useGridDragDrop } from "../../composables/useGridDragDrop.js";
import { useStackOrdering } from "../../composables/useStackOrdering.js";
import { useGridFetch } from "../../composables/useGridFetch.js";
import { useGridKeyboardNav } from "../../composables/useGridKeyboardNav.js";

const emit = defineEmits([
  "open-overlay",
  "refresh-sidebar",
  "clear-search",
  "reset-to-all",
  "search-all",
  "update:selected-sort",
  "update:stack-stats",
  "import-started",
  "import-ended",
  "clear-multi-selection",
  "update:character-multi-mode",
  "update:set-multi-mode",
  "update:set-difference-base-id",
  "update:embed-watermark",
  "update:visible-range-label",
  "load-pending-imports",
  "open-settings",
  "open-import",
  "confirm-export-zip",
]);

// Props
const props = defineProps({
  thumbnailSize: Number,
  sidebarVisible: Boolean,
  backendUrl: String,
  selectedCharacter: { type: [String, Number, null], default: null },
  selectedSet: { type: [Number, String, null], default: null },
  selectedSetIds: { type: Array, default: () => [] },
  searchQuery: String,
  activeCategoryLabel: { type: String, default: "Category" },
  isAllPicturesActive: { type: Boolean, default: false },
  selectedSort: String,
  selectedDescending: Boolean,
  similarityCharacter: { type: [String, Number, null], default: null },
  stackThreshold: { type: [String, Number, null], default: null },
  showStars: Boolean,
  showFaceBboxes: Boolean,
  showProblemIcon: Boolean,
  penalisedTagWeights: { type: Object, default: () => ({}) },
  showStacks: { type: Boolean, default: true },
  compactMode: { type: Boolean, default: false },
  themeMode: { type: String, default: "light" },
  dateFormat: { type: String, default: "locale" },
  allPicturesId: String,
  unassignedPicturesId: String,
  scrapheapPicturesId: String,
  gridVersion: { type: Number, default: 0 },
  wsUpdateKey: { type: Number, default: 0 },
  wsTagUpdate: {
    type: Object,
    default: () => ({ key: 0, pictureIds: [] }),
  },
  wsPluginProgress: {
    type: Object,
    default: () => ({ key: 0, payload: null }),
  },
  mediaTypeFilter: { type: String, default: "all" },
  comfyuiModelFilter: { type: Array, default: () => [] },
  comfyuiLoraFilter: { type: Array, default: () => [] },
  comfyuiConfigured: { type: Boolean, default: false },
  minScoreFilter: { type: Number, default: null },
  maxScoreFilter: { type: Number, default: null },
  smartScoreBucketFilter: { type: String, default: null },
  resolutionBucketFilter: { type: String, default: null },
  tagFilter: { type: Array, default: () => [] },
  tagRejectedFilter: { type: Array, default: () => [] },
  tagConfidenceAboveFilter: { type: Array, default: () => [] },
  tagConfidenceBelowFilter: { type: Array, default: () => [] },
  faceBboxFilter: { type: String, default: null },
  sharedOnlyFilter: { type: Boolean, default: false },
  unassignedOnlyFilter: { type: Boolean, default: false },
  columns: { type: Number, required: true },
  hiddenTags: { type: Array, default: () => [] },
  applyTagFilter: { type: Boolean, default: false },
  projectViewMode: { type: String, default: "global" },
  selectedProjectId: { type: Number, default: null },
  characterProjectIds: { type: Object, default: () => ({}) },
  setProjectIds: { type: Object, default: () => ({}) },
  referenceFolderIdFilter: { type: Number, default: null },
  filePathPrefixFilter: { type: String, default: null },
  importSourceFolderFilter: { type: String, default: null },
  folderScanning: { type: Boolean, default: false },
  selectedCharacterIds: { type: Array, default: () => [] },
  characterMultiMode: { type: String, default: "union" },
  setMultiMode: { type: String, default: "intersection" },
  setDifferenceBaseId: { type: Number, default: null },
  selectedSetNames: { type: Object, default: () => ({}) },
  publicUrl: { type: String, default: null },
  embedWatermark: { type: Boolean, default: false },
  pendingExternalImportCount: { type: Number, default: 0 },
});

// ============================================================
// CONSTANTS
// ============================================================
const LIKENESS_GROUPS_SORT_KEY = "LIKENESS_GROUPS";
const MIN_THUMBNAIL_SIZE = 128;
const MAX_THUMBNAIL_SIZE = 384;
const THUMBNAIL_INFO_ROW_HEIGHT = 24;

const normalizedSelectedSetIds = computed(() => {
  const idsFromProp = Array.isArray(props.selectedSetIds)
    ? props.selectedSetIds
    : [];
  const normalized = idsFromProp
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  if (normalized.length > 0) {
    return Array.from(new Set(normalized));
  }
  const single = Number(props.selectedSet);
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
  const ids = Array.isArray(props.selectedCharacterIds)
    ? props.selectedCharacterIds
    : [];
  return ids
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0)
    .sort((a, b) => a - b);
});
const isMultiCharacterView = computed(
  () => normalizedSelectedCharacterIds.value.length > 1,
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
const contextMenuVisible = ref(false);
const contextMenuX = ref(0);
const contextMenuY = ref(0);
const contextMenuImage = ref(null);
const sharedPictureIds = ref(new Set());
const revokeSharesDialogOpen = ref(false);
const revokeSharesPending = ref(null); // { pictureId }
// Share picture dialog
const sharePicDialogOpen = ref(false);

// ============================================================
// GRID DATA STATE
// ============================================================

// Badge size interpolated continuously across column count (1 = lg, 12+ = sm).
const badgeSizeT = computed(() =>
  Math.min(1, Math.max(0, ((props.columns || 1) - 1) / 11)),
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

const badgeIconSizes = computed(() => {
  const t = badgeSizeT.value;
  return {
    stack: Math.round(24 - 12 * t),
    penalised: Math.round(24 - 12 * t),
    star: Math.round(22 - 12 * t),
  };
});

const allGridImages = ref([]);

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
    const res = await apiClient.get(`${props.backendUrl}/pictures/plugins`);
    const plugins = Array.isArray(res.data?.plugins) ? res.data.plugins : [];
    availablePlugins.value = plugins.filter((plugin) => plugin && plugin.name);
  } catch (err) {
    console.warn("Failed to load image plugins:", err);
    availablePlugins.value = [];
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
  // Build per-image captions from stored descriptions in the grid.
  const idSet = new Set(pictureIds);
  const idToDesc = new Map();
  for (const img of allGridImages.value) {
    const id = Number(img?.id);
    if (idSet.has(id)) idToDesc.set(id, img.description || "");
  }
  const captions = pictureIds.map((id) => idToDesc.get(id) ?? "");
  runPluginWithParameters(pluginName, pictureIds, parameters, captions);
}

async function runPluginWithParameters(
  pluginName,
  pictureIds,
  parameters,
  captions,
) {
  if (!pluginName || !Array.isArray(pictureIds) || !pictureIds.length) return;
  try {
    const res = await apiClient.post(
      `${props.backendUrl}/pictures/plugins/${encodeURIComponent(pluginName)}`,
      {
        picture_ids: pictureIds,
        parameters: parameters || {},
        captions: Array.isArray(captions) ? captions : undefined,
      },
    );
    const createdIds = Array.isArray(res.data?.created_picture_ids)
      ? res.data.created_picture_ids
      : [];
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
    alert(err?.response?.data?.detail || err?.message || String(err));
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
      props.selectedProjectId != null ? props.selectedProjectId : undefined;
    const rawChar = props.selectedCharacter;
    const specialIds = [
      props.allPicturesId,
      props.unassignedPicturesId,
      props.scrapheapPicturesId,
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
    const res = await apiClient.post(
      `${props.backendUrl}/comfyui/run_t2i`,
      payload,
    );
    const prompts = Array.isArray(res.data?.prompts) ? res.data.prompts : [];
    handleComfyuiRun({ prompts });
  } catch (err) {
    console.error("ComfyUI T2I run failed:", err);
  }
}

function onComfyuiRefreshGrid({ preserveScroll } = {}) {
  if (preserveScroll) preserveScrollOnNextFetch.value = true;
  debouncedFetchAllGridImages();
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
    props.searchQuery && props.searchQuery.trim()
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
    smartScoreProgress.message =
      props.searchQuery && props.searchQuery.trim()
        ? "Search complete"
        : `Sorted by ${getSortProgressLabel(smartScoreProgressSortKey.value)}`;
    setTimeout(() => {
      if (Number(loadId) !== smartScoreProgressLoadId) return;
      smartScoreProgress.visible = false;
      setSmartScoreProgressPercent(0, { allowReset: true });
      smartScoreProgress.message = "Calculating smart scores";
      smartScoreProgressSortKey.value = "";
    }, SORT_PROGRESS_COMPLETION_HOLD_MS);
    return;
  }
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
  () => props.wsUpdateKey,
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
  () => props.wsTagUpdate,
  (payload) => {
    if (!payload || typeof payload !== "object") return;
    const nextKey = payload.key || 0;
    if (!nextKey || nextKey === lastWsTagUpdateKey.value) return;
    lastWsTagUpdateKey.value = nextKey;
    const pictureIds = Array.isArray(payload.pictureIds)
      ? payload.pictureIds
      : [];
    // Only refresh the grid when a tag filter is active — without a filter,
    // tagging doesn't change anything visible in the grid (thumbnails and sort
    // order are unaffected), so refreshing just hammers the DB for no benefit.
    if (
      !(props.tagFilter && props.tagFilter.length) &&
      !(props.tagRejectedFilter && props.tagRejectedFilter.length) &&
      !(
        props.tagConfidenceAboveFilter && props.tagConfidenceAboveFilter.length
      ) &&
      !(props.tagConfidenceBelowFilter && props.tagConfidenceBelowFilter.length)
    )
      return;
    if (pauseGridAutoUpdates.value) {
      pendingGridRefreshAfterImport.value = true;
      return;
    }
    // Coalesce all task-driven tag updates into an infrequent full refresh to
    // avoid starving the tagger when a large grid is open.
    scheduleWsTagFullRefresh();
  },
);

watch(
  () => props.wsPluginProgress,
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
function onThumbnailLoad(id, event = null) {
  thumbnailLoadedMap[id] = (thumbnailLoadedMap[id] || 0) + 1;
  const assignedAt = Number(thumbnailAssignedAtMap[id]);
  const eventTarget = event?.target || null;
  const src =
    eventTarget?.currentSrc ||
    eventTarget?.src ||
    thumbnailRefs[id]?.currentSrc ||
    thumbnailRefs[id]?.src ||
    null;
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
  // requests to different URLs for the same file — the browser aborts one,
  // leaving the overlay's <video> element stuck loading.
  return `${props.backendUrl}/pictures/${img.id}.${img.format.toLowerCase()}`;
}

// ============================================================
// FACE BBOX FUNCTIONS
// ============================================================
// selectedFaceIds, isFaceSelected, toggleFaceSelection, clearFaceSelection,
// onFaceBboxDragStart — moved to useMultiSelect composable.

// Helper to calculate face bbox overlay style using object-fit: cover logic
function getFaceBboxStyle(bbox, idx, img, el, isSelected) {
  if (!el) return { display: "none" };
  const container = el.parentElement;
  if (!container) return { display: "none" };
  const containerWidth = container.clientWidth;
  const containerHeight = container.clientHeight;
  const naturalWidth = img.thumbnail_width || img.width || 1;
  const naturalHeight = img.thumbnail_height || img.height || 1;
  // Calculate scale and offset for object-fit: cover
  const scale = Math.max(
    containerWidth / naturalWidth,
    containerHeight / naturalHeight,
  );
  const displayWidth = naturalWidth * scale;
  const offsetX = (containerWidth - displayWidth) / 2;
  const offsetY = 0;
  // Transform bbox
  const left = offsetX + bbox[0] * scale;
  const top = offsetY + bbox[1] * scale;
  const width = (bbox[2] - bbox[0]) * scale;
  const height = (bbox[3] - bbox[1]) * scale;
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
    !props.showFaceBboxes ||
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

// Track which image is currently hovered
// ============================================================
// HOVER STATE + THUMBNAIL INFO DISPLAY ITEMS
// ============================================================
const hoveredImageIdx = ref(null);
const hoveredStackId = ref(null);

function handleImageMouseEnter(img) {
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
    typeof props.selectedSort === "string" ? props.selectedSort : "";

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
    typeof props.searchQuery === "string" &&
    img.likeness_score !== undefined
  ) {
    items.push({
      key: "search_likeness",
      text: `Search likeness: ${img.likeness_score.toFixed(2)}`,
    });
  } else if (selectedSort === "IMPORTED_AT" && img.imported_at) {
    items.push({
      key: "imported_at",
      text: formatUserDate(img.imported_at, props.dateFormat),
    });
  } else if (selectedSort.includes("DATE") && img.created_at) {
    items.push({
      key: "created_at",
      text: formatUserDate(img.created_at, props.dateFormat),
    });
  } else if (
    selectedSort === LIKENESS_GROUPS_SORT_KEY &&
    (typeof img.stackIndex === "number" || typeof img.stack_index === "number")
  ) {
    if (!props.showStacks) {
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
    typeof props.dateFormat === "string" ? props.dateFormat : "locale";
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
    typeof props.dateFormat === "string" ? props.dateFormat : "locale";
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

function getCompactGroupLabel(img, visualIdx) {
  if (!props.compactMode || !img) return null;
  const isSearchMode = !!(props.searchQuery && props.searchQuery.trim());
  const sort = typeof props.selectedSort === "string" ? props.selectedSort : "";

  function getGroupKey(item) {
    if (!item) return null;
    if (isSearchMode && typeof item.likeness_score === "number")
      return Math.round(item.likeness_score * 20);
    if (sort === "IMPORTED_AT" && item.imported_at)
      return item.imported_at.slice(0, 19);
    if (sort.includes("DATE") && item.created_at)
      return item.created_at.slice(0, 10);
    const smartScore = getGridSmartScoreValue(item);
    if (sort.includes("SMART_SCORE") && smartScore !== null)
      return Math.round(smartScore * 10);
    if (
      sort.includes("CHARACTER_LIKENESS") &&
      typeof item.character_likeness === "number"
    )
      return Math.round(item.character_likeness * 100);
    if (sort === "TEXT_CONTENT" && typeof item.text_score === "number")
      return Math.round(item.text_score * 10);
    return null;
  }

  const currentKey = getGroupKey(img);
  if (currentKey === null) return null;

  const prevImg =
    visualIdx > 0 ? gridImagesToRender.value[visualIdx - 1] : null;
  if (visualIdx > 0 && getGroupKey(prevImg) === currentKey) return null;

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
    return `≈ ${(Math.floor(img.character_likeness * 100) / 100).toFixed(2)}`;
  if (sort === "TEXT_CONTENT" && typeof img.text_score === "number")
    return `${(img.text_score * 100).toFixed(0)}%`;
  return null;
}

const compactStickyLabel = computed(() => {
  if (!props.compactMode) return null;
  const isSearchMode = !!(props.searchQuery && props.searchQuery.trim());
  const sort = typeof props.selectedSort === "string" ? props.selectedSort : "";

  function getGroupKey(item) {
    if (!item) return null;
    if (isSearchMode && typeof item.likeness_score === "number")
      return Math.round(item.likeness_score * 20);
    if (sort === "IMPORTED_AT" && item.imported_at)
      return item.imported_at.slice(0, 19);
    if (sort.includes("DATE") && item.created_at)
      return item.created_at.slice(0, 10);
    const smartScore = getGridSmartScoreValue(item);
    if (sort.includes("SMART_SCORE") && smartScore !== null)
      return Math.round(smartScore * 10);
    if (
      sort.includes("CHARACTER_LIKENESS") &&
      typeof item.character_likeness === "number"
    )
      return Math.round(item.character_likeness * 100);
    if (sort === "TEXT_CONTENT" && typeof item.text_score === "number")
      return Math.round(item.text_score * 10);
    return null;
  }

  const firstVisibleVisualIdx = visibleStart.value - renderStart.value;
  const firstImg = gridImagesToRender.value?.[firstVisibleVisualIdx];
  if (!firstImg) return null;

  // Suppress if this item already has a between-row pill (it's a group boundary)
  const prevImg =
    firstVisibleVisualIdx > 0
      ? gridImagesToRender.value[firstVisibleVisualIdx - 1]
      : null;
  const isGroupBoundary =
    firstVisibleVisualIdx === 0 ||
    getGroupKey(prevImg) !== getGroupKey(firstImg);
  if (isGroupBoundary) return null;

  if (isSearchMode && typeof firstImg.likeness_score === "number")
    return `≈ ${firstImg.likeness_score.toFixed(2)}`;
  if (sort === "IMPORTED_AT" && firstImg.imported_at)
    return formatCompactDatetime(firstImg.imported_at);
  if (sort.includes("DATE") && firstImg.created_at)
    return formatCompactDate(firstImg.created_at);
  const smartScore = getGridSmartScoreValue(firstImg);
  if (sort.includes("SMART_SCORE") && smartScore !== null)
    return `★ ${(Math.round(smartScore * 10) / 10).toFixed(1)}`;
  if (
    sort.includes("CHARACTER_LIKENESS") &&
    typeof firstImg.character_likeness === "number"
  )
    return `≈ ${(Math.floor(firstImg.character_likeness * 100) / 100).toFixed(2)}`;
  if (sort === "TEXT_CONTENT" && typeof firstImg.text_score === "number")
    return `${(firstImg.text_score * 100).toFixed(0)}%`;
  if (
    sort === "TAG_UNCERTAINTY" &&
    typeof firstImg.tag_uncertainty === "number"
  )
    return `⚠ ${(firstImg.tag_uncertainty * 100).toFixed(0)}%`;
  if (
    sort === "ANOMALY_TAG_UNCERTAINTY" &&
    typeof firstImg.anomaly_tag_uncertainty === "number"
  )
    return `⚠ ${(firstImg.anomaly_tag_uncertainty * 100).toFixed(0)}%`;
  return null;
});

// ── Visible range label (emitted to SelectionBar) ──────────────
function getImageSortLabel(img) {
  if (!img) return null;
  const isSearchMode = !!(props.searchQuery && props.searchQuery.trim());
  const sort = typeof props.selectedSort === "string" ? props.selectedSort : "";
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

watch(
  visibleRangeLabel,
  (label) => {
    emit("update:visible-range-label", label);
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
function handleImageError(event) {
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

// clearSelection — moved to useMultiSelect composable.
// getDragSelectionIds/setupMultiExportDrag/prepareThumbnailNativeDrag/handleThumbnailPointerRelease — moved to useGridDragDrop composable.

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
  const backendUrl = props.backendUrl;
  const faceIds = selectedFaceIds.value
    .map((entry) => entry.faceId)
    .filter((id) => id !== undefined && id !== null);
  const pictureIds = selectedImageIds.value.slice();
  if (isScrapheapView.value) {
    if (!pictureIds.length) {
      clearFaceSelection();
      return;
    }
    apiClient
      .post(`${backendUrl}/pictures/scrapheap/restore`, {
        picture_ids: pictureIds,
      })
      .catch((err) => {
        alert(`Error restoring images: ${err.message}`);
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
    props.selectedCharacter &&
    props.selectedCharacter !== props.allPicturesId &&
    props.selectedCharacter !== props.unassignedPicturesId
  ) {
    const requests = [];
    if (pictureIds.length) {
      requests.push(
        apiClient.delete(
          `${backendUrl}/characters/${props.selectedCharacter}/faces`,
          {
            data: { picture_ids: pictureIds },
          },
        ),
      );
    }
    if (faceIds.length) {
      requests.push(
        apiClient.delete(
          `${backendUrl}/characters/${props.selectedCharacter}/faces`,
          {
            data: { face_ids: faceIds },
          },
        ),
      );
    }
    if (!requests.length) return;
    Promise.all(requests)
      .catch((err) => {
        alert(`Error removing faces from character: ${err.message}`);
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
    props.selectedSet &&
    props.selectedSet !== props.allPicturesId &&
    props.selectedSet !== props.unassignedPicturesId
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
          const res = await apiClient.get(`${backendUrl}/stacks/${stackId}`);
          memberIds = res.data?.picture_ids ?? [];
        } catch {
          // Fallback: only remove the originally-selected picture(s).
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
        [...idsToRemoveFromSet].map(
          (id) =>
            apiClient
              .delete(
                `${backendUrl}/picture_sets/${props.selectedSet}/members/${id}`,
              )
              .catch(() => {}), // silently ignore if picture not in set
        ),
      );

      // For expanded stacks: also remove the selected picture(s) from the
      // stack itself so they become standalone images.
      if (stackRemovalsForExpanded.size) {
        await Promise.all(
          [...stackRemovalsForExpanded.entries()].map(([stackId, ids]) =>
            apiClient
              .delete(`${backendUrl}/stacks/${stackId}/members`, {
                data: { picture_ids: ids },
              })
              .catch((err) =>
                console.error("Failed to remove from stack:", err),
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
      alert(`Error removing images from set: ${err.message}`);
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
    props.selectedCharacter === props.unassignedPicturesId &&
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
    props.selectedCharacter === props.unassignedPicturesId &&
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

function handleRemoveFromCharacter(payload) {
  const pictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
    : [];
  if (!pictureIds.length) return;
  const removedCharId = payload?.characterId;
  const currentChar = props.selectedCharacter;
  const isInRemovedCharView =
    removedCharId != null &&
    currentChar != null &&
    String(currentChar) === String(removedCharId) &&
    currentChar !== props.allPicturesId &&
    currentChar !== props.unassignedPicturesId &&
    currentChar !== props.scrapheapPicturesId;
  if (isInRemovedCharView) {
    removeImagesById(pictureIds);
    selectedImageIds.value = [];
    clearFaceSelection();
    lastSelectedImageId.value = null;
    updateVisibleThumbnails();
  }
  emit("refresh-sidebar");
}

async function deleteSelected() {
  if (!selectedImageIds.value.length) return;
  const isScrapheapSelection = isScrapheapView.value;

  // For non-scrapheap deletions, expand collapsed stacks to all their members
  // while only deleting the selected pictures from expanded stacks.
  let idsToRemove;
  if (!isScrapheapSelection) {
    const imageById = new Map(
      (allGridImages.value || [])
        .filter((img) => img && img.id != null)
        .map((img) => [String(img.id), img]),
    );

    const resolved = new Set();
    const collapsedStackIds = new Set();

    for (const id of selectedImageIds.value) {
      const img = imageById.get(String(id));
      const stackId = getPictureStackId(img);
      if (!stackId || expandedStackIds.value.has(stackId)) {
        // No stack, or stack is expanded: delete only this picture.
        resolved.add(id);
      } else {
        // Collapsed stack: delete all members.
        collapsedStackIds.add(stackId);
      }
    }

    for (const stackId of collapsedStackIds) {
      try {
        const res = await apiClient.get(
          `${props.backendUrl}/stacks/${stackId}`,
        );
        const memberIds = res.data?.picture_ids;
        if (Array.isArray(memberIds) && memberIds.length) {
          for (const mid of memberIds) resolved.add(mid);
        }
      } catch (e) {
        console.error(
          "Failed to fetch stack members for delete, falling back to selected ids:",
          e,
        );
        // Fallback: delete the originally-selected picture(s) from this stack.
        for (const id of selectedImageIds.value) {
          const img = imageById.get(String(id));
          if (getPictureStackId(img) === stackId) resolved.add(id);
        }
      }
    }

    idsToRemove = [...resolved];
  } else {
    idsToRemove = selectedImageIds.value.slice();
  }

  if (isScrapheapSelection) {
    if (
      !confirm(`Permanently delete ${idsToRemove.length} selected image(s)?`)
    ) {
      return;
    }
  }

  const backendUrl = props.backendUrl;
  try {
    if (isScrapheapSelection) {
      await apiClient.delete(`${backendUrl}/pictures/scrapheap`, {
        data: {
          picture_ids: idsToRemove,
        },
      });
    } else {
      await Promise.all(
        idsToRemove.map((id) =>
          apiClient.delete(`${backendUrl}/pictures/${id}`).catch((err) => {
            alert(`Error deleting image ${id}: ${err.message}`);
          }),
        ),
      );
    }
    removeImagesById(idsToRemove);
    selectedImageIds.value = [];
    lastSelectedImageId.value = null;
    if (isScrapheapSelection) {
      updateVisibleThumbnails();
    }
    emit("refresh-sidebar");
  } catch (err) {
    alert(`Error deleting images: ${err?.message || err}`);
  }
}

async function handleSetProjectForSelected(payload) {
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
    window.alert("Invalid project selected.");
    return;
  }

  const action = String(payload?.action || "set").toLowerCase();

  try {
    if (action === "removed") {
      if (nextProjectId === null) {
        return;
      }
      await apiClient.patch(`${props.backendUrl}/pictures/project`, {
        picture_ids: pictureIds,
        project_id: nextProjectId,
        mode: "remove",
      });
    } else if (action === "added") {
      await apiClient.patch(`${props.backendUrl}/pictures/project`, {
        picture_ids: pictureIds,
        project_id: nextProjectId,
        mode: "add",
      });
    } else {
      await apiClient.patch(`${props.backendUrl}/pictures/project`, {
        picture_ids: pictureIds,
        project_id: nextProjectId,
      });
    }

    preserveScrollOnNextFetch.value = true;
    if (overlayOpen.value) {
      // A project change while the overlay is open would replace allGridImages,
      // breaking the filmstrip. Defer the refetch until the overlay closes.
      pendingOverlayGridRefresh.value = true;
    } else {
      await fetchAllGridImages({ force: true });
      updateVisibleThumbnails();
    }
    emit("refresh-sidebar");
  } catch (err) {
    const message = err?.response?.data?.detail || err?.message || String(err);
    window.alert(`Failed to update project association: ${message}`);
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
    props.scrapheapPicturesId || "SCRAPHEAP",
  ).toUpperCase();
  const selected = String(props.selectedCharacter || "").toUpperCase();
  return selected === scrapheapId;
});
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
const scrapheapEmptying = ref(false);
const showSelectionBar = computed(() => {
  return selectedImageIds.value.length > 0 || selectedFaceIds.value.length > 0;
});
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
    String(props.allPicturesId),
    String(props.unassignedPicturesId),
  ].includes(String(props.selectedCharacter ?? ""));

  const isSetCategorySelection =
    props.selectedSet !== null &&
    props.selectedSet !== undefined &&
    String(props.selectedSet) !== "";

  const supportsAuthoritativeCategoryCount =
    isTopCategorySelection || isSetCategorySelection;

  const hasNoAdditionalFilters =
    !(props.searchQuery || "").trim() &&
    props.mediaTypeFilter === "all" &&
    (props.comfyuiModelFilter || []).length === 0 &&
    (props.comfyuiLoraFilter || []).length === 0 &&
    props.minScoreFilter == null &&
    props.maxScoreFilter == null &&
    props.smartScoreBucketFilter == null &&
    props.resolutionBucketFilter == null;

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
const isSelectionEmpty = computed(() => {
  return !showSelectionBar.value;
});
const showScrapheapBar = computed(() => {
  return isScrapheapView.value && isSelectionEmpty.value;
});
const SCRAPHEAP_BAR_HEIGHT_PX = 30;
const wrapperStyle = { position: "relative", height: "100%" };
const scrollWrapperStyle = computed(() => ({
  position: "absolute",
  top: "var(--selbar-height, 48px)",
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

async function confirmEmptyScrapheap() {
  if (scrapheapEmptyDisabled.value) return;
  const confirmed = confirm(
    "Empty scrapheap? This will permanently delete all pictures inside.",
  );
  if (!confirmed) return;
  scrapheapEmptying.value = true;
  try {
    await apiClient.delete(`${props.backendUrl}/pictures/scrapheap`);
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
    alert("Failed to empty scrapheap.");
  } finally {
    scrapheapEmptying.value = false;
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
    await apiClient.post(`${props.backendUrl}/pictures/scrapheap/restore`);
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
    alert("Failed to restore scrapheap.");
  } finally {
    scrapheapRestoring.value = false;
  }
}

async function openReferenceLocation(picId) {
  try {
    await apiClient.post(`${props.backendUrl}/pictures/${picId}/open-location`);
  } catch {
    // silently ignore — the OS might not support it
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
      const selectedSetId = props.selectedSet;
      const selectedCharacterId = props.selectedCharacter;
      const selectedCharacterKey = String(selectedCharacterId ?? "");
      const skipCharacter = [
        String(props.allPicturesId),
        String(props.unassignedPicturesId),
        String(props.scrapheapPicturesId),
      ].includes(selectedCharacterKey);
      if (selectedSetId != null && selectedSetId !== "") {
        await Promise.all(
          pictureIds.map((id) =>
            apiClient.post(
              `${props.backendUrl}/picture_sets/${selectedSetId}/members/${id}`,
            ),
          ),
        );
      } else if (!skipCharacter && selectedCharacterId != null) {
        await apiClient.post(
          `${props.backendUrl}/characters/${selectedCharacterId}/faces`,
          { picture_ids: pictureIds },
        );
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
  () => props.gridVersion,
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
      // Overlay is open — quietly refresh in the background without clearing
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

// ============================================================
// VIEWPORT + RENDER
// ============================================================
// VIEWPORT + RENDER
// ============================================================
const allGridImagesLength = computed(() => allGridImages.value?.length ?? 0);

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
  getGridColumnWidth,
  updateRowHeightFromGrid,
  onGridScroll,
  scrollCursorIntoView,
} = useVirtualScroll(scrollWrapper, gridContainer, props, allGridImagesLength, {
  onVisibleRangeChange: () => updateVisibleThumbnails(),
  afterRowHeightUpdate: () => refreshAllThumbnailInfoDisplays(),
});

// ============================================================
// OVERLAY STATE
// ============================================================
const overlayOpen = ref(false);
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
// When fetchAllGridImages completes while the overlay is open, the resulting
// image list is stored here instead of being written to allGridImages directly.
// Applied to allGridImages when the overlay closes.
const pendingGridImages = ref(null);

// ============================================================
// DRAG & DROP STATE + SOURCE HELPERS
// (moved to useGridDragDrop composable)
// ============================================================
const {
  dragOverlayVisible,
  dragOverlayMessage,
  dragOverlayDepth,
  dragSource,
  dragSourceImageIds,
  setDragSourceImageIds,
  clearDragSourceImageIds,
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
  imagesError,
  totalAllPicturesCount,
  totalCurrentCategoryCount,
  gridReady,
  gridLoadEpoch,
  lastFetchKey,
  lastFetchError,
  lastFetchSuccess,
  smartScoreLoadingVisible,
  buildGridFetchKey,
  buildPictureIdsQueryParams,
  buildLikenessGroupQueryParams,
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
  },
);

// ============================================================
// STACK ORDERING + EXPAND / COLLAPSE + REORDER DRAG
// (moved to useStackOrdering composable)
// ============================================================
const {
  expandedStackIds,
  expandedStackMembers,
  expandedStackLoading,
  stackVisualOrderMap,
  selectedStackId,
  selectedMultipleStackIds,
  showRemoveFromStack,
  mapGridImages,
  getStackCardStyle,
  getStackCardColor,
  getStackBadgeIconStyle,
  getStackBandStyle,
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
  collectExpandableStackIds,
  handleStackReorderDragOver,
  handleStackReorderDragLeave,
  handleStackReorderDrop,
  createStackFromSelection,
  dissolveSelectedStacks,
  removeSelectedFromStack,
  getLikenessGroupId,
  createStacksFromSelectedGroups,
  collapseStackImages,
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
    invalidateVisibleThumbnailRanges,
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
    props.selectedCharacter &&
    props.selectedCharacter !== `${props.allPicturesId}` &&
    props.selectedCharacter !== `${props.unassignedPicturesId}` &&
    props.selectedCharacter !== `${props.scrapheapPicturesId}`
  ) {
    try {
      const res = await apiClient.get(
        `${props.backendUrl}/characters/${props.selectedCharacter}`,
      );
      const char = res.data;
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
      const res = await apiClient.get(
        `${props.backendUrl}/picture_sets/${primarySelectedSetId.value}`,
      );
      const set = res.data;
      name = set.set.name || "";
    } catch (e) {
      console.error("Set fetch failed:", e);
    }
  }
  selectedGroupName.value = name;
}

watch(
  [
    () => props.selectedCharacter,
    () => props.selectedSet,
    () => props.selectedSetIds,
  ],
  () => {
    updateSelectedGroupName();
  },
  { immediate: true },
);

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
    showSelectionBar,
    selectedImageIds,
    lastSelectedImageId,
    cursorIdx,
    isMultiCharacterView,
    isSetOverlapView,
    hoveredImageIdx,
  },
  props,
  emit,
  {
    clearFaceSelection,
    clearSearchQuery,
    scrollCursorIntoView,
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
    const params = new URLSearchParams();
    if (options.smartScore) {
      params.set("smart_score", "true");
    }
    if (options.force) {
      params.set("cb", String(Date.now()));
    }
    const query = params.toString();
    const url = query
      ? `${props.backendUrl}/pictures/${imageId}/metadata?${query}`
      : `${props.backendUrl}/pictures/${imageId}/metadata`;
    const res = await apiClient.get(url);
    const data = await res.data;
    return data;
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
  if (props.selectedSort === LIKENESS_GROUPS_SORT_KEY) {
    const stackIndex = getStackIndexFromItem(allGridImages.value[idx]);
    if (typeof stackIndex === "number") {
      reorderStackByScore(stackIndex);
    }
  }
  invalidateThumbnailIndex(idx);
  fetchThumbnailsBatch(idx, idx + 1);
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
  if ((fields.tags || fields.smartScore) && isSmartScoreSortActive()) {
    if (overlayOpen.value) {
      // Smart-score re-ranking would reorder allGridImages mid-viewing.
      // Defer the full refetch; still refresh the single card's metadata.
      pendingOverlayGridRefresh.value = true;
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
  overlayInitialExpandedStackIds.value = Array.from(
    expandedStackIds.value || [],
  );
  overlayImageId.value = img.id;
  overlayOpen.value = true;
  _pushOverlayRoute(img.id);
}

function closeOverlay() {
  overlayOpen.value = false;
  overlayImageId.value = null;
  overlayInitialExpandedStackIds.value = [];
  _removeOverlayRoute();
  if (comfyuiRunner.value?.comfyuiPendingOverlayRefresh) {
    comfyuiRunner.value.comfyuiPendingOverlayRefresh.value = false;
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
    debouncedFetchAllGridImages();
  }
}

// ============================================================
// SCORING
// ============================================================
async function setScore(img, n) {
  if (isReadOnly.value) {
    setGuestScore(img, n);
    return;
  }
  const newScore = toggleScore(img.score, n);
  applyScore(img, newScore);
}

// ---- Guest scoring helpers ----

function _generateGuestSessionId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback for older browsers — use cryptographically secure random bytes
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant bits
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

function _getOrCreateGuestSessionId() {
  if (guestSessionId.value) return guestSessionId.value;
  const id = _generateGuestSessionId();
  guestSessionId.value = id;
  return id;
}

async function _submitGuestScores(scores, setCookie) {
  const sid = _getOrCreateGuestSessionId();
  const payload = { session_id: sid, set_cookie: setCookie, scores };
  await apiClient.post(`${props.backendUrl}/pictures/guest-scores`, payload);
}

function setGuestScore(img, n) {
  const currentScore = guestScoreMap.value.get(img.id) ?? null;
  const newScore = toggleScore(currentScore, n);

  if (guestConsentState.value === null) {
    // Show consent banner; queue the intent
    pendingGuestScoreIntent.value = { img, newScore };
    guestConsentBannerVisible.value = true;
    return;
  }

  // Optimistic local update
  const updated = new Map(guestScoreMap.value);
  updated.set(img.id, newScore);
  guestScoreMap.value = updated;

  const setCookie = guestConsentState.value === "accepted";
  _submitGuestScores({ [String(img.id)]: newScore }, setCookie)
    .then(() => {
      if (isScoreSortActive()) {
        debouncedFetchAllGridImages({ force: true });
      }
    })
    .catch((err) => {
      console.error("Failed to submit guest score:", err);
    });
}

async function handleGuestConsentAccepted() {
  guestConsentState.value = "accepted";
  guestConsentBannerVisible.value = false;
  // Do not persist the guest session identifier in localStorage.
  // The server-managed HttpOnly cookie handles durable session continuity;
  // keeping the session_id only in memory is sufficient for the current visit.
  const intent = pendingGuestScoreIntent.value;
  pendingGuestScoreIntent.value = null;
  if (intent) {
    const updated = new Map(guestScoreMap.value);
    updated.set(intent.img.id, intent.newScore);
    guestScoreMap.value = updated;
    await _submitGuestScores({ [String(intent.img.id)]: intent.newScore }, true)
      .then(() => {
        if (isScoreSortActive()) debouncedFetchAllGridImages({ force: true });
      })
      .catch((err) => console.error("Failed to submit guest score:", err));
  }
}

function handleGuestConsentRejected() {
  guestConsentState.value = "rejected";
  guestConsentBannerVisible.value = false;
  // Do NOT persist the session ID anywhere — if the user reloads they get a
  // brand-new session with no connection to these scores.
  const intent = pendingGuestScoreIntent.value;
  pendingGuestScoreIntent.value = null;
  if (intent) {
    const updated = new Map(guestScoreMap.value);
    updated.set(intent.img.id, intent.newScore);
    guestScoreMap.value = updated;
    _submitGuestScores({ [String(intent.img.id)]: intent.newScore }, false)
      .then(() => {
        if (isScoreSortActive()) debouncedFetchAllGridImages({ force: true });
      })
      .catch((err) => console.error("Failed to submit guest score:", err));
  }
}

async function fetchGuestScores() {
  // Kept only as a fallback / explicit refresh. The main listing
  // (GET /pictures) now overlays guest scores onto img.score server-side.
  try {
    const resp = await apiClient.get(
      `${props.backendUrl}/pictures/guest-scores`,
    );
    const scores = resp?.data?.scores ?? {};
    const map = new Map();
    for (const [k, v] of Object.entries(scores)) {
      map.set(Number(k), v);
    }
    guestScoreMap.value = map;
  } catch (err) {
    console.error("[guest-scores] Failed to fetch guest scores:", err);
  }
}

function initGuestSession() {
  const readOnly = isReadOnly.value;
  const cookies = document.cookie;
  const ls = localStorage.getItem("guest_session_id");
  if (!readOnly) return;
  // A non-HttpOnly sentinel cookie is set alongside the HttpOnly guest_session
  // cookie when the user accepted persistent storage.
  const hasCookieConsent = cookies
    .split(";")
    .some((c) => c.trim().startsWith("guest_session_active=1"));
  if (hasCookieConsent) {
    guestConsentState.value = "accepted";
    // Restore the session ID from localStorage so POST bodies stay in sync
    // with the HttpOnly cookie the server already knows about.
    if (ls) {
      guestSessionId.value = ls;
    }
    // Scores are now overlaid by the backend in GET /pictures, so
    // fetchAllGridImages() will include them in img.score directly.
    // fetchGuestScores() is only needed to pre-populate guestScoreMap
    // for optimistic-update display before the grid loads.
    fetchGuestScores();
    return;
  }
  // No cookie consent — fresh start.  The banner will appear on first score.
  // (Rejected users are intentionally not remembered across page loads.)
}

function isScoreSortActive() {
  return typeof props.selectedSort === "string"
    ? props.selectedSort.toUpperCase() === "SCORE"
    : false;
}

function isCharacterLikenessSortActive() {
  return typeof props.selectedSort === "string"
    ? props.selectedSort.toUpperCase() === "CHARACTER_LIKENESS"
    : false;
}

function isSmartScoreSortActive() {
  return typeof props.selectedSort === "string"
    ? props.selectedSort.toUpperCase().includes("SMART_SCORE")
    : false;
}

function getGridSmartScoreValue(img) {
  if (!img) return null;
  const raw =
    typeof img.smartScore === "number"
      ? img.smartScore
      : typeof img.smart_score === "number"
        ? img.smart_score
        : null;
  return Number.isFinite(raw) ? raw : null;
}

function formatGridSmartScoreValue(img) {
  const value = getGridSmartScoreValue(img);
  return value === null ? "" : value.toFixed(2);
}

function invalidateVisibleThumbnailRanges() {
  const start = Math.max(0, visibleStart.value - renderBuffer.value);
  const end = Math.min(
    allGridImages.value.length,
    visibleEnd.value + renderBuffer.value,
  );
  loadedRanges.value = loadedRanges.value.filter(
    ([rangeStart, rangeEnd]) => rangeEnd <= start || rangeStart >= end,
  );
  updateVisibleThumbnails();
}

function _spliceAndReinsert(
  items,
  currentIndex,
  target,
  targetScore,
  getScore,
  descending,
) {
  items.splice(currentIndex, 1);
  let insertIndex = items.findIndex((item) => {
    const score = getScore(item);
    return descending ? score < targetScore : score > targetScore;
  });
  if (insertIndex === -1) insertIndex = items.length;
  if (insertIndex === currentIndex) {
    const updated = allGridImages.value.slice();
    updated[currentIndex] = { ...target, idx: currentIndex };
    allGridImages.value = updated;
    return null;
  }
  items.splice(insertIndex, 0, target);
  for (let i = 0; i < items.length; i += 1) {
    items[i].idx = i;
  }
  allGridImages.value = items;
  invalidateVisibleThumbnailRanges();
  return insertIndex;
}

function repositionImageByScore(imageId, newScore) {
  const items = allGridImages.value.slice();
  const dId = getPictureId(imageId);
  const currentIndex = items.findIndex(
    (item) => getPictureId(item?.id) === dId,
  );
  if (currentIndex === -1) return;

  const target = items[currentIndex];
  target.score = newScore;
  const targetScore = newScore ?? 0;
  const descending = props.selectedDescending === true;
  const insertIndex = _spliceAndReinsert(
    items,
    currentIndex,
    target,
    targetScore,
    (item) => item.score ?? 0,
    descending,
  );
  if (insertIndex !== null) {
    nextTick(() => {
      const grid = gridContainer.value;
      if (!grid) return;
      const card = grid.querySelectorAll(".image-card")[insertIndex];
      if (card && card.scrollIntoView) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
  }
}

let smartScoreRepositioning = false;

function repositionImageBySmartScore(imageId, smartScore, latestInfo = null) {
  if (smartScoreRepositioning) return;
  smartScoreRepositioning = true;
  try {
    const items = allGridImages.value.slice();
    const currentIndex = items.findIndex((item) => item.id === imageId);
    if (currentIndex === -1) return;

    const targetScore = smartScore ?? 0;
    const target = {
      ...items[currentIndex],
      ...(latestInfo && typeof latestInfo === "object" ? latestInfo : {}),
      smartScore: targetScore,
      thumbnail:
        items[currentIndex]?.thumbnail ?? latestInfo?.thumbnail ?? null,
    };
    const descending = props.selectedDescending === true;
    _spliceAndReinsert(
      items,
      currentIndex,
      target,
      targetScore,
      (item) => item.smartScore ?? 0,
      descending,
    );
  } finally {
    smartScoreRepositioning = false;
  }
}

async function refreshSmartScoreForImage(imageId) {
  if (!imageId || !isSmartScoreSortActive()) return;
  const latestInfo = await fetchImageInfo(imageId, { smartScore: true });
  if (!latestInfo || Array.isArray(latestInfo)) return;

  const idx = allGridImages.value.findIndex((img) => img?.id === imageId);
  if (idx !== -1) {
    const current = allGridImages.value[idx] || {};
    const smartScore =
      typeof latestInfo.smartScore === "number" ? latestInfo.smartScore : null;
    if (current.smartScore === smartScore) {
      return;
    }
    await nextTick();
    await new Promise((resolve) => requestAnimationFrame(resolve));
    repositionImageBySmartScore(imageId, smartScore ?? 0, latestInfo);
  }
}

async function applyScoresByEntries(entries, options = {}) {
  const { updateSort = true, emitRefreshSidebar = true } = options;
  if (!Array.isArray(entries) || !entries.length) return;

  // Score updates are applied locally below (including score-sort reordering),
  // so the immediate WS gridVersion refresh would be redundant and can clear
  // current multi-selection in some paths. Skip that next WS refresh.
  skipNextWsRefresh.value = true;

  const scoresPayload = {};
  for (const [id, score] of entries) {
    scoresPayload[String(id)] = Number(score);
  }

  await apiClient.post(`${props.backendUrl}/pictures/apply-scores`, {
    scores: scoresPayload,
    only_unscored: false,
  });

  const scoreMap = new Map(
    entries.map(([id, score]) => [String(id), Number(score)]),
  );

  let updatedImages = allGridImages.value.map((img) => {
    if (!img || img.id == null) return img;
    const key = String(img.id);
    if (!scoreMap.has(key)) return img;
    return { ...img, score: scoreMap.get(key) };
  });

  if (updateSort && isScoreSortActive()) {
    const descending = props.selectedDescending === true;
    updatedImages = updatedImages
      .slice()
      .sort((a, b) => {
        const aScore = a?.score ?? 0;
        const bScore = b?.score ?? 0;
        if (aScore === bScore) {
          const aIdx = a?.idx ?? 0;
          const bIdx = b?.idx ?? 0;
          return aIdx - bIdx;
        }
        return descending ? bScore - aScore : aScore - bScore;
      })
      .map((img, idx) => (img ? { ...img, idx } : img));
    allGridImages.value = updatedImages;
    invalidateVisibleThumbnailRanges();
  } else {
    allGridImages.value = updatedImages;
  }

  if (updateSort && isCharacterLikenessSortActive()) {
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages();
  }

  if (updateSort && isSmartScoreSortActive()) {
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages();
  }

  if (emitRefreshSidebar) {
    emit("refresh-sidebar");
  }
}

async function applyScore(img, newScore) {
  const imageId = img?.id;
  if (!imageId) {
    alert("Failed to set score: image id is missing.");
    return;
  }
  try {
    // Suppress the WS-driven gridVersion reload that the score apply triggers;
    // the score update is already applied locally by applyScoresByEntries.
    skipNextWsRefresh.value = true;
    await applyScoresByEntries([[String(imageId), newScore]], {
      updateSort: false,
      emitRefreshSidebar: false,
    });

    if (isScoreSortActive()) {
      if (overlayOpen.value) {
        // Reordering the grid while the overlay is open would break the
        // filmstrip. Defer the reposition until the overlay closes.
        pendingOverlayGridRefresh.value = true;
      } else {
        repositionImageByScore(imageId, newScore);
      }
    }
    if (isCharacterLikenessSortActive()) {
      if (overlayOpen.value) {
        pendingOverlayGridRefresh.value = true;
        return;
      }
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
      return;
    }
    if (isSmartScoreSortActive()) {
      if (overlayOpen.value) {
        pendingOverlayGridRefresh.value = true;
        return;
      }
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
      return;
    }
  } catch (e) {
    alert(e.message);
  }
}

async function applyScoresForSelection(imageIds, targetScore) {
  const ids = Array.isArray(imageIds) ? imageIds.filter(Boolean) : [];
  if (!ids.length) return;
  if (!Number.isFinite(targetScore)) return;

  const gridById = new Map(
    allGridImages.value
      .filter((img) => img && img.id != null)
      .map((img) => [String(img.id), img]),
  );

  const entries = [];
  for (const id of ids) {
    const key = String(id);
    const img = gridById.get(key);
    if (!img) continue;
    const current = Number(img.score || 0);
    const nextScore = toggleScore(current, targetScore);
    entries.push([key, nextScore]);
  }

  if (!entries.length) return;

  await applyScoresByEntries(entries, {
    updateSort: true,
    emitRefreshSidebar: true,
  });
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
    () => props.selectedCharacter,
    () => props.selectedSet,
    () => props.selectedSetIds,
    () => props.characterMultiMode,
    () => props.setMultiMode,
    () => props.setDifferenceBaseId,
    () => props.projectViewMode,
    () => props.selectedProjectId,
    () => props.searchQuery,
    () => props.selectedSort,
    () => props.similarityCharacter,
    () => props.stackThreshold,
  ],
  () => {
    _resetGridState();
    updateSelectedGroupName();
    fetchAllPicturesCount();
    debouncedFetchAllGridImages.cancel();
    fetchAllGridImages({ force: true, showProgress: true });
  },
);

watch(
  [
    () => props.mediaTypeFilter,
    () => props.comfyuiModelFilter,
    () => props.comfyuiLoraFilter,
    () => props.minScoreFilter,
    () => props.maxScoreFilter,
    () => props.smartScoreBucketFilter,
    () => props.resolutionBucketFilter,
    () => props.tagFilter,
    () => props.tagRejectedFilter,
    () => props.tagConfidenceAboveFilter,
    () => props.tagConfidenceBelowFilter,
    () => props.faceBboxFilter,
    () => props.sharedOnlyFilter,
    () => props.unassignedOnlyFilter,
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
  () => props.showStacks,
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
  () => props.columns,
  async () => {
    updateRowHeightFromGrid();
    updateVisibleThumbnails();
    await nextTick();
    triggerFaceOverlayRedraw();
    requestAnimationFrame(() => {
      triggerFaceOverlayRedraw();
    });
  },
);

watch(
  () => props.compactMode,
  () => {
    updateRowHeightFromGrid();
    updateVisibleThumbnails();
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
  [() => props.showFaceBboxes, () => allGridImages.value.length],
  ([faceEnabled, length], [prevFace, prevLength]) => {
    if (!faceEnabled) return;
    if (length <= 0) return;
    if (faceEnabled === prevFace && length === prevLength) {
      return;
    }
    invalidateVisibleThumbnailRanges();
  },
);

// ============================================================
// MEDIA FILTERING + EMPTY STATE
// ============================================================
function filterImagesByMediaType(images) {
  let filtered = images;
  if (props.mediaTypeFilter === "images") {
    filtered = filtered.filter((img) => {
      if (!img) return false;
      const candidates = [img.name, img.id, img.format]
        .filter(Boolean)
        .map((v) => (typeof v === "string" ? v : ""));
      return candidates.some((val) => isSupportedImageFile(val));
    });
  } else if (props.mediaTypeFilter === "videos") {
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
  if (props.folderScanning) return false;
  return (
    gridReady.value &&
    !imagesLoading.value &&
    filteredGridCount.value === 0 &&
    emptyStateDelayPassed.value
  );
});

const showFolderScanningState = computed(() => {
  return (
    props.folderScanning && gridReady.value && filteredGridCount.value === 0
  );
});

const canShowAllPicturesButton = computed(() => {
  return totalAllPicturesCount.value > 0;
});

const emptyStateTitle = computed(() => {
  if (isSetOverlapView.value) {
    return "No overlap";
  }
  if (isScrapheapView.value) {
    return "No pictures in the scrap heap";
  }
  return totalAllPicturesCount.value > 0
    ? "No pictures match the current filters"
    : "No pictures in the database.";
});

const emptyStateSubtitle = computed(() => {
  if (isSetOverlapView.value) {
    return "The picture sets have no overlap.";
  }
  if (isScrapheapView.value) {
    return "Are all your pictures that good?";
  }
  return totalAllPicturesCount.value > 0
    ? "Try clearing filters, adjusting your search, or switching sets."
    : "Add pictures by dragging them here.";
});

const emptyStateImage = computed(() => {
  return isScrapheapView.value ? "/EmptyTrash.png" : "/Empty.png";
});

const emptyStateAlt = computed(() => {
  return isScrapheapView.value ? "Empty scrap heap" : "No images";
});

const isDarkThemeActive = computed(() => {
  const mode = String(props.themeMode || "light").toLowerCase();
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

  if (loading || count > 0 || props.folderScanning) {
    emptyStateDelayPassed.value = false;
    return;
  }

  emptyStateDelayPassed.value = false;
  emptyStateDelayTimer = setTimeout(() => {
    if (
      !imagesLoading.value &&
      filteredGridCount.value === 0 &&
      !props.folderScanning
    ) {
      emptyStateDelayPassed.value = true;
    }
  }, EMPTY_STATE_DELAY_MS);
});

// When scanning ends the grid will reload; reset the delay so the
// empty state only shows after images have had a chance to arrive.
watch(
  () => props.folderScanning,
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
      penalised_tags: Array.isArray(img?.penalised_tags)
        ? img.penalised_tags
        : [],
      thumbnail_width: img?.thumbnail_width,
      thumbnail_height: img?.thumbnail_height,
    }));
    // Synchronously pre-fill thumbnail URLs from imported_at so <img> elements
    // render immediately without waiting for the POST round trip. The POST
    // still runs to enrich face overlays and penalised-tag hints.
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
    // Separate images: those missing a thumbnail need a full fetch; those that
    // already have one still need penalised_tags refreshed (e.g. after a tag
    // removal on a stack-head image whose thumbnail was carried over from the
    // previous grid state and was therefore never re-fetched).
    const missingThumbIds = new Set(
      gridImages
        .filter(
          (img) => img.id !== null && img.id !== undefined && !img.thumbnail,
        )
        .map((img) => String(img.id)),
    );
    ids = Array.from(
      new Set(
        gridImages
          .filter((img) => img.id !== null && img.id !== undefined)
          .map((img) => String(img.id)),
      ),
    );
    const requestedIdPreview = ids.slice(0, 8);
    let overlayNeedsRedraw = false;
    if (ids.length) {
      const thumbRes = await apiClient.post(
        `${props.backendUrl}/pictures/thumbnails`,
        JSON.stringify({ ids }),
      );
      const thumbData = await thumbRes.data;
      if (requestEpoch !== thumbnailRequestEpoch.value) {
        return;
      }
      const requestedIds = new Set(ids);
      for (const gridImg of gridImages) {
        if (!requestedIds.has(String(gridImg.id))) {
          continue;
        }
        const thumbObj = thumbData[String(gridImg.id)];
        // Only overwrite thumbnail URL for images that didn't already have one.
        if (missingThumbIds.has(String(gridImg.id))) {
          const thumbnailUrl =
            thumbObj && thumbObj.thumbnail ? thumbObj.thumbnail : null;
          const previousThumbnail = gridImg.thumbnail || null;
          gridImg.thumbnail = thumbnailUrl
            ? appendShareToken(
                thumbnailUrl.startsWith("http")
                  ? thumbnailUrl
                  : `${props.backendUrl}${thumbnailUrl}`,
              )
            : null;
          if (
            gridImg.id != null &&
            gridImg.thumbnail &&
            gridImg.thumbnail !== previousThumbnail
          ) {
            thumbnailAssignedAtMap[gridImg.id] = performance.now();
          }
          if (gridImg.id != null && thumbObj && thumbObj.thumbnail) {
            thumbnailLoadedMap[gridImg.id] =
              (thumbnailLoadedMap[gridImg.id] || 0) + 1;
          }
        }
        // Always refresh faces, thumbnail dimensions, and penalised_tags
        // from authoritative server data, even when the thumbnail URL was
        // pre-filled from imported_at.
        if (thumbObj) {
          const thumbWidth = Number(thumbObj.thumbnail_width);
          const thumbHeight = Number(thumbObj.thumbnail_height);
          if (!Number.isNaN(thumbWidth) && thumbWidth > 0) {
            gridImg.thumbnail_width = thumbWidth;
          }
          if (!Number.isNaN(thumbHeight) && thumbHeight > 0) {
            gridImg.thumbnail_height = thumbHeight;
          }
        }
        gridImg.faces =
          thumbObj && Array.isArray(thumbObj.faces) ? thumbObj.faces : [];
        if (props.showFaceBboxes && gridImg.faces.length) {
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
    for (let i = 0; i < gridImages.length; i++) {
      const img = gridImages[i];
      img.idx = start + i; // Redundant but explicit for safety
      // Skip null-id slots: the snapshot was taken before the grid was fully
      // populated and a concurrent BG batch may have since written real data
      // into this slot. Writing a stale null-id object would wipe that data.
      if (img.id == null) {
        continue;
      }
      allGridImages.value[start + i] = img;
      if (img.thumbnail) {
        clearThumbnailRetry(img.id);
      } else {
        scheduleThumbnailRetry(img.id, start + i, requestEpoch);
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
  let start = Math.max(0, visibleStart.value - renderBuffer.value);
  let end = Math.min(
    allGridImages.value.length,
    visibleEnd.value + renderBuffer.value,
  );
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
  // Suppress the synthesized click that fires right after a long-press touchend
  if (suppressTouchClickId.value === img.id) {
    suppressTouchClickId.value = null;
    return;
  }
  cursorIdx.value = idx;
  const isCtrl = event.ctrlKey || event.metaKey;
  const isShift = event.shiftKey;
  let newSelection = [];
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
      .filter(Boolean);
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
  if (isReadOnly.value) return;
  if (!selectedImageIds.value.includes(img.id)) {
    selectedImageIds.value = [img.id];
    lastSelectedImageId.value = img.id;
  }
  contextMenuImage.value = img;
  contextMenuX.value = event.clientX;
  contextMenuY.value = event.clientY;
  contextMenuVisible.value = true;
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
      const res = await apiClient.post(
        `${props.backendUrl}/users/me/shared-picture-ids/batch`,
        { picture_ids: ids },
      );
      const shared = new Set(res.data?.shared_ids ?? []);
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
      // Non-critical — silently ignore
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
    await apiClient.delete(
      `${props.backendUrl}/users/me/tokens/by-resource?resource_type=picture&resource_id=${pending.pictureId}`,
    );
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
  if (props.applyTagFilter) {
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

async function removeTagFromImage(imageId, tag) {
  if (!imageId) {
    console.error("Image ID is required to remove a tag.");
    return;
  }

  try {
    const tagId = getTagId(tag);
    if (tagId == null) {
      console.warn("Tag id is required to remove a tag.", tag);
      return;
    }
    const tagKey = String(tagId);
    await apiClient.delete(
      `${props.backendUrl}/pictures/${imageId}/tags/${tagKey}`,
    );
    const gridImg = allGridImages.value.find(
      (img) => img && img.id === imageId,
    );
    if (gridImg && Array.isArray(gridImg.tags)) {
      const d = getTagList(gridImg.tags);
      gridImg.tags = d.filter((t) => !tagMatches(t, tag));
    }
    await _afterTagMutation(imageId);
  } catch (error) {
    console.error("Error removing tag:", error);
  }
}

async function addTagToImage(imageId, tag) {
  try {
    const response = await apiClient.post(
      `${props.backendUrl}/pictures/${imageId}/tags`,
      {
        tag: tag,
      },
    );
    const responseTags = getTagList(response?.data?.tags);
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
  () => props.thumbnailSize,
  () => {
    // Recalculate visibleStart and visibleEnd after rowHeight update
    nextTick(() => {
      updateRowHeightFromGrid();
      const el = scrollWrapper.value;
      if (!el) return;
      let cardHeight = rowHeight.value;
      const scrollTop = el.scrollTop;
      const cols = props.columns;
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
  getExportCount,
  removeImagesById,
  clearFaceSelection,
  runComfyuiOnGridImages,
  hasCursorFocus: computed(() => cursorIdx.value !== null),
});

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
  let url = `${props.backendUrl}/pictures/export`;
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
  const extraParamString = extraParams.toString();
  if (params) {
    url += `?${params}`;
    if (extraParamString) {
      url += `&${extraParamString}`;
    }
  } else if (extraParamString) {
    url += `?${extraParamString}`;
  }

  try {
    exportProgress.visible = true;
    exportProgress.status = "starting";
    exportProgress.processed = 0;
    exportProgress.total = 0;
    exportProgress.message = "Preparing export...";
    exportProgress.cancelRequested = false;

    const startRes = await apiClient.get(url);
    const taskId = startRes?.data?.task_id;
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
      const statusRes = await apiClient.get(
        `${props.backendUrl}/pictures/export/status`,
        { params: { task_id: taskId } },
      );
      const status = statusRes?.data?.status;
      exportProgress.status = status || "in_progress";
      exportProgress.processed = statusRes?.data?.processed || 0;
      exportProgress.total = statusRes?.data?.total || 0;
      exportProgress.message =
        status === "completed"
          ? "Finalizing download..."
          : "Exporting images...";
      if (status === "completed") {
        downloadUrl = statusRes?.data?.download_url;
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

    const fileRes = await apiClient.get(`${props.backendUrl}${downloadUrl}`, {
      responseType: "blob",
    });

    let filename = "pixlstash_export.zip";
    const disposition = fileRes.headers["content-disposition"];
    if (disposition) {
      const match = disposition.match(/filename="?([^";]+)"?/);
      if (match) filename = match[1];
    }

    const link = document.createElement("a");
    link.href = URL.createObjectURL(fileRes.data);
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
    alert("Export failed: " + (e.message || e));
    setTimeout(() => {
      exportProgress.visible = false;
      exportProgress.status = "idle";
      exportProgress.message = "";
    }, 4000);
  }
}

function abortExportZip() {
  if (!exportProgress.visible) return;
  exportProgress.cancelRequested = true;
}

// ============================================================
// SEARCH
// ============================================================
function clearSearchQuery() {
  emit("clear-search", "");
}

function handleEmptyStateReset() {
  gridReady.value = false;
  emptyStateDelayPassed.value = false;
  emit("reset-to-all");
}
</script>

<style scoped>
.drag-overlay {
  position: sticky;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(var(--v-theme-accent), 0.2);
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  border: 8px solid rgb(var(--v-theme-accent));
  border-radius: 16px;
  box-sizing: border-box;
  transition:
    border-color 0.2s,
    background 0.2s;
  color: rgb(var(--v-theme-on-accent));
  font-size: 3em;
  font-weight: bold;
}

.drag-overlay-message {
  padding: 6px 14px;
  background: rgba(var(--v-theme-shadow), 0.35);
  border-radius: 12px;
}

.thumbnail-badge {
  background: rgba(var(--v-theme-surface), 0.92);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.22);
  border-radius: 8px;
  color: rgb(var(--v-theme-on-surface));
  box-shadow: 0 2px 6px rgba(var(--v-theme-shadow), 0.3);
  font-size: var(--badge-font-size, 0.8em);
  padding: var(--badge-padding, 2px 4px);
  z-index: 30;
  max-width: 90%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.thumbnail-badge--top-left,
.thumbnail-badge--top-right,
.thumbnail-badge--bottom-left,
.thumbnail-badge--bottom-right {
  position: absolute;
}

.thumbnail-badge--top-left {
  top: 2px;
  left: 2px;
}

.thumbnail-badge--top-right {
  top: 2px;
  right: 2px;
}

.thumbnail-badge--bottom-left {
  left: 2px;
  bottom: 2px;
}

.thumbnail-bottom-left-badges {
  position: absolute;
  bottom: 2px;
  left: 2px;
  display: flex;
  gap: 3px;
  align-items: center;
  max-width: 90%;
}

.thumbnail-bottom-left-badges > .thumbnail-badge {
  max-width: 100%;
}

/* Format and resolution badges are hover-only */
.resolution-hover-overlay,
.thumbnail-id-overlay {
  opacity: 0;
  transition: opacity 0.12s ease;
}

.image-card:hover .resolution-hover-overlay,
.image-card:hover .thumbnail-id-overlay {
  opacity: 1;
}

.thumbnail-reference-badge {
  opacity: 1;
  display: flex;
  align-items: center;
  cursor: pointer;
  padding-left: 2px;
  background: none !important;
  border: none !important;
  box-shadow: none !important;
}

.thumbnail-reference-badge .v-icon {
  color: rgb(var(--v-theme-primary)) !important;
}

.thumbnail-share-badge .v-icon {
  color: rgb(var(--v-theme-accent)) !important;
}

.thumbnail-share-badge {
  opacity: 1;
  display: flex;
  align-items: center;
  padding-left: 2px;
  background: none !important;
  border: none !important;
  box-shadow: none !important;
}

.thumbnail-badge--bottom-right {
  right: 2px;
  bottom: 2px;
}

.thumbnail-badge--bottom-right-raised {
  bottom: 22px;
}

.likeness-group-indicator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.face-bbox-label {
  font-size: 0.7em;
  background-color: rgba(var(--v-theme-surface), 0.3);
  color: rgb(var(--v-theme-on-surface));
  text-overflow: ellipsis;
  overflow-y: hidden;
  overflow-x: hidden;
  white-space: nowrap;
}

.grid-content-area {
  --selbar-height: 48px;
}

@media (hover: none) and (pointer: coarse) {
  .grid-content-area {
    --selbar-height: 56px;
  }
}

/* ── Visible range pill overlay ── */
.grid-range-pill {
  position: absolute;
  top: calc(var(--selbar-height, 48px) + 10px);
  left: 50%;
  transform: translateX(-50%);
  background: rgba(var(--v-theme-surface), 0.82);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 999px;
  color: rgb(var(--v-theme-on-surface));
  font-size: 0.72em;
  font-weight: 600;
  line-height: 1;
  padding: 4px 12px;
  white-space: nowrap;
  backdrop-filter: blur(6px);
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.22);
  pointer-events: none;
  user-select: none;
  z-index: 50;
}
.grid-loading-more-pill {
  position: absolute;
  top: calc(var(--selbar-height, 48px) + 10px);
  right: 16px;
  background: rgba(var(--v-theme-surface), 0.82);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 999px;
  color: rgb(var(--v-theme-on-surface));
  font-size: 0.72em;
  font-weight: 600;
  line-height: 1;
  padding: 4px 12px;
  white-space: nowrap;
  backdrop-filter: blur(6px);
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.22);
  pointer-events: none;
  user-select: none;
  z-index: 50;
}
.grid-range-fade-enter-active,
.grid-range-fade-leave-active {
  transition: opacity 0.15s ease;
}
.grid-range-fade-enter-from,
.grid-range-fade-leave-to {
  opacity: 0;
}

.pending-imports-pill-anchor {
  position: sticky;
  top: 0;
  z-index: 100;
  height: 0;
  overflow: visible;
  display: flex;
  justify-content: center;
  pointer-events: none;
}

.pending-imports-pill {
  position: absolute;
  top: 8px;
  pointer-events: all;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(var(--v-theme-primary), 0.92);
  color: #fff;
  padding: 6px 18px;
  border-radius: 9999px;
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
  border: none;
  user-select: none;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.35);
  white-space: nowrap;
  transition: opacity 0.15s;
}

.pending-imports-pill:hover {
  opacity: 0.88;
}

.grid-scroll-wrapper {
  overflow-y: auto;
  overflow-x: hidden;
  width: 100%;
  padding-right: 0px;
  scrollbar-color: rgb(var(--v-theme-accent)) rgb(var(--v-theme-on-accent));
}
.empty-state {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: auto;
  z-index: 5;
}
.empty-state-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 26px 30px;
  border-radius: 18px;
  border: 1px dashed rgba(var(--v-theme-border), 0.5);
  background: rgba(var(--v-theme-panel), 0.72);
  color: rgb(var(--v-theme-on-background));
  text-align: center;
  max-width: 420px;
  box-shadow: 0 10px 30px rgba(var(--v-theme-shadow), 0.08);
  pointer-events: auto;
}
.empty-state-illustration {
  color: rgba(var(--v-theme-on-panel), 0.45);
}
.empty-state-title {
  font-size: 1.2em;
  font-weight: 600;
}
.empty-state-subtitle {
  font-size: 0.95em;
  opacity: 0.8;
}
.empty-state-action {
  margin-top: 6px;
}
.image-grid {
  height: 100%;
  display: grid;
  gap: 4px;
  width: 100%;
  box-sizing: border-box;
  flex: 1 1 0%;
  padding: 0 4px 2px 0 !important;
  align-content: start;
  justify-content: start;
}
.compact-mode.image-grid {
  padding-top: 0 !important;
  gap: 0px;
}
.grid-scroll-wrapper::-webkit-scrollbar {
  width: 10px;
}
.image-card-cursor .thumbnail-img {
  outline: 2px solid rgba(var(--v-theme-primary), 0.9);
  outline-offset: -2px;
}

.image-card {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  margin-bottom: 2.2em;
  padding: 0px;
  margin: 0;
  transition:
    box-shadow 0.2s,
    border 0.2s,
    z-index 0s;
  position: relative;
  z-index: 0;
  border: 0px solid transparent;
}

.image-card:hover {
  z-index: 20;
}

.image-card-stack-reorder-left::after,
.image-card-stack-reorder-right::after {
  content: "";
  position: absolute;
  top: 8px;
  bottom: 32px;
  width: 6px;
  border-radius: 999px;
  background: rgba(var(--v-theme-accent), 0.95);
  box-shadow: 0 2px 8px rgba(var(--v-theme-accent), 0.45);
  pointer-events: none;
  z-index: 6;
}

.image-card-stack-reorder-left::after {
  left: -3px;
}

.image-card-stack-reorder-right::after {
  right: -3px;
}

.selection-overlay {
  position: absolute;
  inset: 0;
  background: rgba(var(--v-theme-info), 0.38);
  pointer-events: none;
  z-index: 25;
  border-radius: 8px;
  transition: none;
}
.image-card:hover .selection-overlay,
.stack-hover-active .selection-overlay {
}

/* Touch select mode: show a checkmark badge on each selected image */
.touch-select-mode .image-card {
  user-select: none;
  -webkit-user-select: none;
}

/* Suppress Safari's image save / link callout on all image cards */
.image-card {
  -webkit-touch-callout: none;
}
/* Suppress all hover-revealed elements in touch-select mode */
.touch-select-mode .image-card:hover .resolution-hover-overlay,
.touch-select-mode .image-card:hover .thumbnail-id-overlay,
.touch-select-mode .image-card:hover .thumbnail-top-right-badges {
  opacity: 0 !important;
  pointer-events: none !important;
}
.touch-select-mode .image-card .selection-overlay::after {
  content: "✓";
  position: absolute;
  top: 6px;
  right: 6px;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: rgb(var(--v-theme-info));
  color: rgb(var(--v-theme-on-info));
  font-size: 16px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 26px;
  text-align: center;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
}
/* Empty circle on unselected images in touch-select mode */
.touch-select-mode .image-card::after {
  content: "";
  position: absolute;
  top: 6px;
  right: 6px;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.85);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.35);
  pointer-events: none;
  z-index: 26;
}
.touch-select-mode .image-card:has(.selection-overlay)::after {
  display: none;
}
.thumbnail-info-row {
  margin-top: 2px;
  text-align: center;
  height: 24px;
  min-height: 24px;
  max-height: 24px;
  overflow: hidden;
  background: none;
  width: 100%;
}
.thumbnail-info {
  font-size: 0.88em;
  color: rgba(var(--v-theme-on-background), 0.78);
  text-align: center;
  line-height: 24px;
  display: block;
  width: 100%;
  max-width: 100%;
  padding: 0 8px;
  white-space: nowrap;
  overflow: hidden;
  text-shadow: 0 1px 1px rgba(var(--v-theme-shadow), 0.12);
}
.thumbnail-container {
  width: 100%;
  height: 100%;
  position: relative;
  aspect-ratio: 1 / 1;
}
.thumbnail-container::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: 8px;
  box-shadow: inset 0 0 12px 4px rgba(var(--v-theme-accent), 0.3);
  opacity: 0;
  transition: opacity 0.15s ease;
  z-index: 22;
  pointer-events: none;
}
.image-card:hover .thumbnail-container::after,
.stack-hover-active .thumbnail-container::after {
  opacity: 1;
}
.compact-mode .thumbnail-container::after {
  border-radius: 0;
}

.thumbnail-container-drag-source .thumbnail-img,
.thumbnail-container-drag-source .thumbnail-placeholder {
  filter: grayscale(1) brightness(0.65);
  opacity: 0.75;
}
.thumbnail-img {
  width: 100%;
  height: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
  object-position: top center;
  display: block;
  border-radius: 8px;
  position: absolute;
  top: 0;
  left: 0;
  z-index: 1;
  box-shadow: 1px 2px 3px 3px rgba(var(--v-theme-shadow), 0.3);
  transition: box-shadow 0.18s;
}
.thumbnail-img:hover {
  box-shadow:
    1px 2px 3px 3px rgba(var(--v-theme-shadow), 0.3),
    inset 0 0 4px 4px rgba(var(--v-theme-accent), 0.45);
  z-index: 20;
}
.stack-hover-active .thumbnail-img {
  box-shadow:
    1px 2px 3px 3px rgba(var(--v-theme-shadow), 0.3),
    inset 0 0 4px 4px rgba(var(--v-theme-accent), 0.45);
  z-index: 20;
}
.thumbnail-card {
  width: 100%;
  max-width: none;
  min-width: none;
  position: relative;
  padding: 4px;
}

/* Compact mode: no info row gap, no rounded corners, no shadow */
.compact-mode .thumbnail-card {
  padding: 0px 0px 0px 0px;
}

.compact-mode .thumbnail-img {
  border-radius: 0;
  box-shadow: none;
}
.compact-mode .thumbnail-img:hover {
  box-shadow: inset 0 0 4px 4px rgba(var(--v-theme-accent), 0.45);
}
.compact-sticky-label,
.compact-group-label {
  transform: translateX(-50%) translateY(4px);
  background: rgba(var(--v-theme-surface), 0.82);
  color: rgb(var(--v-theme-accent));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.18);
  border-radius: 999px;
  padding: 1px 9px;
  font-size: 0.72em;
  font-weight: 600;
  line-height: 1.6;
  white-space: nowrap;
  max-width: 80%;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
  z-index: 400;
  box-shadow: 0 1px 4px rgba(var(--v-theme-shadow), 0.25);
  backdrop-filter: blur(4px);
}
.compact-sticky-label {
  position: sticky;
  top: 0;
  left: 50%;
  width: fit-content;
  transform: translateX(-50%) translateY(4px);
  margin-bottom: -24px;
}
.compact-group-label {
  position: absolute;
  top: 0;
  left: 30%;
}

.thumbnail-card-new {
  animation: gridNewPulse 2.2s ease-out;
  box-shadow: 0 0 0 rgba(var(--v-theme-accent), 0);
}

@keyframes gridNewPulse {
  0% {
    transform: translateZ(0) scale(1);
    box-shadow: 0 0 0 rgba(var(--v-theme-accent), 0);
  }
  35% {
    transform: translateZ(0) scale(1.015);
    box-shadow:
      0 0 10px rgba(var(--v-theme-accent), 0.5),
      0 0 18px rgba(var(--v-theme-accent), 0.25);
  }
  100% {
    transform: translateZ(0) scale(1);
    box-shadow: 0 0 0 rgba(var(--v-theme-accent), 0);
  }
}
/* Overlay for image index on thumbnail */
.thumbnail-index-overlay {
  pointer-events: none;
}

.thumbnail-drag-preview {
  position: fixed;
  width: 160px;
  height: auto;
  opacity: 0.01;
  pointer-events: none;
  left: -9999px;
  top: -9999px;
  object-fit: cover;
  border-radius: 8px;
}

.penalised-tag-indicator,
.stack-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 30;
  pointer-events: auto;
  padding: 0;
}

.penalised-tag-indicator {
  background: none !important;
  border: none !important;
  box-shadow: none !important;
}

.penalised-tag-indicator .v-icon {
  filter: drop-shadow(0 0 1.5px rgba(0, 0, 0, 0.55));
}

.stack-indicator {
  cursor: pointer;
}

.stack-band-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 10;
  border-radius: inherit;
}

/* Top-left permanent badges column (reference, share, problem) */
.thumbnail-top-left-badges {
  position: absolute;
  top: 2px;
  left: 2px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  z-index: 30;
  pointer-events: auto;
}

/* Top-right hover badges column (stars, stack) */
.thumbnail-top-right-badges {
  position: absolute;
  top: 2px;
  right: 2px;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  z-index: 120;
  opacity: 0;
  transition: opacity 0.15s ease;
  pointer-events: none;
}

.image-card:hover .thumbnail-top-right-badges {
  opacity: 1;
  pointer-events: auto;
}

.thumbnail-placeholder {
  width: 100%;
  height: 100%;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: absolute;
  top: 0;
  left: 0;
  color: rgb(var(--v-theme-on-background));
}

.thumbnail-placeholder-icon {
  font-size: 28px;
  opacity: 0.7;
  animation: thumbnailPlaceholderSpin 1.1s linear infinite;
}

@keyframes thumbnailPlaceholderSpin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.multi-select-toolbar {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 300;
  display: flex;
  align-items: center;
  gap: 0;
  height: 36px;
  background: rgb(var(--v-theme-surface-variant));
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  color: rgb(var(--v-theme-on-surface-variant));
  font-size: 13px;
}

.multi-select-toolbar__mode {
  height: 100%;
  padding: 0 10px;
  border: none;
  border-right: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  outline: none;
  appearance: auto;
  flex: 0 0 auto;
}

.multi-select-toolbar__label {
  padding: 0 12px;
  white-space: nowrap;
  color: rgb(var(--v-theme-on-surface-variant));
  font-size: 13px;
}

.multi-select-toolbar__spacer {
  flex: 1;
}

.multi-select-toolbar__separator {
  padding: 0 2px;
  color: rgba(var(--v-theme-on-surface), 0.3);
  font-size: 13px;
  flex: 0 0 auto;
  user-select: none;
}

.multi-select-toolbar__base-label {
  padding: 0 6px 0 4px;
  font-size: 12px;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface-variant));
  white-space: nowrap;
  flex: 0 0 auto;
}

.multi-select-toolbar__base {
  height: 100%;
  padding: 0 8px;
  border: none;
  border-right: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  background: transparent;
  color: rgb(var(--v-theme-on-surface-variant));
  font-size: 13px;
  cursor: pointer;
  outline: none;
  appearance: auto;
  flex: 0 0 auto;
}

.multi-select-toolbar__clear {
  display: flex;
  align-items: center;
  gap: 5px;
  height: 100%;
  padding: 0 14px;
  border: none;
  border-left: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  background: transparent;
  color: rgb(var(--v-theme-on-surface-variant));
  font-size: 13px;
  cursor: pointer;
  flex: 0 0 auto;
  transition: background 0.15s;
}

.multi-select-toolbar__clear:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

@media (max-width: 900px) {
  .multi-select-toolbar {
    height: 40px;
    font-size: 12px;
  }
  .multi-select-toolbar__mode {
    font-size: 12px;
  }
}

/* ── Shared-link indicator on thumbnail ──────────────────────────────── */
.thumbnail-share-badge {
  opacity: 0.65;
  padding: 1px 2px;
  display: flex;
  align-items: center;
  color: rgb(var(--v-theme-primary));
}
</style>

<style>
/* Non-scoped so pseudo-element selectors aren't weakened by the data-v attribute */
/* Thumb uses transparent border + background-clip trick: expands on hover, colour stays the same */
.grid-scroll-wrapper::-webkit-scrollbar-thumb {
  background: rgb(var(--v-theme-accent)) !important;
  background-clip: padding-box !important;
  border: 3px solid transparent !important;
  border-radius: 8px !important;
  transition: border-width 0.15s ease !important;
}
.grid-scroll-wrapper::-webkit-scrollbar-thumb:hover {
  background: rgb(var(--v-theme-accent)) !important;
  background-clip: padding-box !important;
  border: 1px solid transparent !important;
  border-radius: 8px !important;
}
.grid-scroll-wrapper::-webkit-scrollbar-track {
  background: rgba(var(--v-theme-shadow), 0.15) !important;
}
.grid-scroll-wrapper::-webkit-scrollbar-corner {
  background: transparent !important;
}
</style>
