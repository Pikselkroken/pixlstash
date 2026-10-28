<template>
  <!-- The lightbox opens inside the frame (#2163): teleported into App.vue's
       host over the centre column and the right rail, so the title bar and
       the left sidebar never move. Drawn in place when there is no host (a
       component test mounting the overlay on its own). -->
  <Teleport :to="lightboxHost" :disabled="!lightboxHost">
  <div
    v-if="open"
    class="image-overlay"
    @pointerdown.capture="noteRootPointerDown"
    @mousedown.capture="notePressOrigin"
    @mousedown="preventMiddleAutoscroll"
    @auxclick="handleMiddleClick"
    @click.self="handleBackdropClick"
  >
    <div
      class="overlay-shell"
      :class="{
        'chrome-hidden': chromeHidden,
        'sidebar-open': inspectorShown,
        'overlay-shell--fullscreen': fullscreen,
        'overlay-shell--bar-down': fullscreen && fullscreenBarShown,
      }"
      @mousemove="handleMouseActivity"
      @mousedown="handleMouseActivity"
      @click="handleOverlayClick"
      @wheel.passive="handleWheelActivity"
    >
      <!-- Toolbar, stage and filmstrip: the centre column. -->
      <div class="overlay-column">
      <!-- The grid's toolbar line, its contents swapped: the way back, where
           the picture sits in the view, then the picture's own tools and the
           rail toggle in the grid's place. No rating here: it heads the rail. -->
      <header class="overlay-topbar" role="toolbar" aria-label="Picture">
        <AppBarButton
          class="overlay-back"
          icon="chevron-left"
          aria-keyshortcuts="Escape"
          tooltip="Back to the grid (Esc)"
          @click="emit('close')"
        >
          <span class="overlay-back-label">{{ viewLabel }}</span>
          <kbd class="overlay-kbd" aria-hidden="true">Esc</kbd>
        </AppBarButton>
        <span class="bar-separator" aria-hidden="true"></span>
        <AppBarButton
          icon="chevron-left"
          tooltip="Previous (←)"
          :disabled="!canStep"
          @click.stop="showPrevImage"
        />
        <span class="overlay-position num" aria-live="polite">{{
          positionLabel
        }}</span>
        <AppBarButton
          icon="chevron-right"
          tooltip="Next (→)"
          :disabled="!canStep"
          @click.stop="showNextImage"
        />
        <!-- Where this picture sits in its stack, and the one-click way to
             make it the cover without a trip to the filmstrip. -->
        <div v-if="image && overlayStackPlace" class="overlay-stack-chip">
          <v-icon size="16">mdi-layers-outline</v-icon>
          <span
            >{{ overlayStackPlace.position + 1 }} of
            {{ overlayStackPlace.count }} in this stack</span
          >
          <button
            v-if="!isReadOnly && overlayStackPlace.position > 0"
            type="button"
            class="overlay-stack-chip-btn"
            @click.stop="moveStackMember(image.id, 'cover')"
          >
            <Tooltip
              text="Make this the cover of its stack"
              activator="parent"
              :describe="false"
            />
            <v-icon size="14">mdi-star-outline</v-icon>
            Make cover
          </button>
        </div>
        <Tooltip
          v-if="image && isCurrentLocked"
          location="bottom"
          :text="currentLockReason"
          :describe="false"
        >
          <template #activator="{ props: lockTipProps }">
            <div
              v-bind="lockTipProps"
              class="overlay-lock-chip"
              role="img"
              :aria-label="currentLockReason"
            >
              <v-icon size="16">mdi-lock-outline</v-icon>
              <span class="overlay-lock-chip-text">Locked</span>
            </div>
          </template>
        </Tooltip>
        <span class="overlay-topbar-spacer"></span>

        <!-- Set and Project float their menus (`float-menu`): the topbar
             scrolls sideways, so it clips anything drawn in place below it. -->
        <Tooltip
          v-if="image && !isReadOnly"
          :text="stackGroupingLockReason || ''"
        >
          <template #activator="{ props: setTipProps }">
            <AddToEntityControl
              v-bind="withRef(setTipProps, (el) => (addToSetControlRef = el))"
              type="set"
              float-menu
              :key="addToSetControlKey"
              :subject-ids="[image.id]"
              :include-deleted-members="true"
              :disabled="!!stackGroupingLockReason"
              :locked-set-ids="lockedSetsStore.lockedSetIds"
              @added="(payload) => emit('added-to-set', payload)"
            />
          </template>
        </Tooltip>
        <Tooltip
          v-if="image && !isReadOnly"
          :text="stackGroupingLockReason || ''"
        >
          <template #activator="{ props: projectTipProps }">
            <AddToEntityControl
              v-bind="projectTipProps"
              type="project"
              float-menu
              :subject-ids="[image.id]"
              :include-deleted-members="true"
              :expand-stacks="false"
              :disabled="!!stackGroupingLockReason"
              @selected="(payload) => emit('set-project', payload)"
            />
          </template>
        </Tooltip>
        <!-- Segmentation boxes stay on while this is pressed; starting a
             Segment run presses it (`showSegmentation`). -->
        <AppBarButton
          v-if="image"
          class="overlay-segmentation-btn"
          icon="shape-outline"
          tooltip="Segmentation boxes"
          :active="showDetections"
          :aria-pressed="showDetections ? 'true' : 'false'"
          @click.stop="showDetections = !showDetections"
        />
        <!-- Compare two pictures Ctrl-clicked in the filmstrip. Shown greyed
             until there are two: the tip says how to pick them. -->
        <AppBarButton
          v-if="image"
          class="overlay-compare-btn"
          icon="compare-horizontal"
          :tooltip="filmstripCompareReason || 'Compare the two pictures picked'"
          :aria-disabled="filmstripCompareReason ? 'true' : undefined"
          @click.stop="compareFilmstripPicks"
        />
        <!-- The zoom readout lives ON the control (owner ruling): a live
             whole-percent of natural size beside the retained icon. The
             label width is reserved once (5ch, tabular numerals) so the
             toolbar never jumps as the value changes. Click semantics match
             Z: at fit → snap to 100%, otherwise → snap to fit. -->
        <AppBarButton
          class="zoom-btn"
          icon="magnify"
          :aria-label="zoomButtonTitle"
          :tooltip="zoomButtonTitle"
          @click="toggleZoomSnap"
        >
          <span class="zoom-btn-label">{{ zoomButtonLabel }}</span>
        </AppBarButton>
        <!-- Full screen (F): the real one, title bar and sidebar gone. In it
             this bar comes down from the top edge and ends in the way out. -->
        <AppBarButton
          v-if="!fullscreen"
          class="overlay-fullscreen-btn"
          icon="fullscreen"
          tooltip="Full screen (F)"
          aria-keyshortcuts="F"
          @click.stop="toggleFullscreen"
        />
        <!-- Narrow windows have no rail, so the rating comes back here as a
             menu. -->
        <v-menu
          v-if="image && isMobile"
          v-model="starMenuOpen"
          location="bottom end"
          origin="top end"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <AppBarButton
              v-bind="menuProps"
              class="overlay-star-mobile-btn"
              icon="star"
              tooltip="Set rating (1–5)"
              :open="starMenuOpen"
            >
              <span class="overlay-star-mobile-label">{{
                isReadOnly ? guestScore || 0 : image?.score || 0
              }}</span>
            </AppBarButton>
          </template>
          <div class="overlay-star-menu ctx-menu" role="menu" aria-label="Rating">
            <button
              v-for="n in [0, 1, 2, 3, 4, 5]"
              :key="n"
              class="ctx-item"
              role="menuitemradio"
              :aria-checked="
                (isReadOnly ? guestScore || 0 : image?.score || 0) === n
                  ? 'true'
                  : 'false'
              "
              type="button"
              @click.stop="setScore(n)"
            >
              <span class="overlay-star-menu-stars" aria-hidden="true">
                <v-icon
                  v-for="s in 5"
                  :key="s"
                  size="16"
                  :color="
                    s <= n
                      ? 'rgba(var(--v-theme-accent))'
                      : 'rgba(var(--v-theme-on-surface), 0.2)'
                  "
                  >mdi-star</v-icon
                >
              </span>
              <span class="ctx-label-text">{{
                n === 0 ? "No rating" : n
              }}</span>
              <v-icon
                v-if="(isReadOnly ? guestScore || 0 : image?.score || 0) === n"
                class="ctx-check"
                >mdi-check</v-icon
              >
            </button>
          </div>
        </v-menu>
        <!-- The grid's rail toggle, in the grid's place, driving the
             lightbox's own open flag (`lightboxRailOpen`). -->
        <span v-if="!fullscreen" class="overlay-topbar-sidebar-toggle">
          <span class="bar-separator" aria-hidden="true"></span>
          <TbGlobalActions rail="lightbox" rail-name="Picture details" />
        </span>
        <template v-else>
          <span class="bar-separator" aria-hidden="true"></span>
          <AppBarButton
            class="overlay-fullscreen-exit"
            icon="fullscreen-exit"
            aria-keyshortcuts="Escape"
            tooltip="Leave full screen (Esc)"
            @click.stop="toggleFullscreen"
          >
            Exit full screen
            <kbd class="overlay-kbd" aria-hidden="true">Esc</kbd>
          </AppBarButton>
        </template>
      </header>

      <div ref="overlayMainRef" class="overlay-main">
        <div
          v-if="comfyuiProgress && comfyuiProgress.visible && !editTabShowsRun"
          class="overlay-progress overlay-progress--comfyui"
        >
          <div class="overlay-progress-title">
            {{ comfyuiProgress.message }}
          </div>
          <div class="overlay-progress-bar">
            <div
              class="overlay-progress-fill"
              :style="{ width: `${comfyuiProgressPercent}%` }"
            ></div>
          </div>
        </div>

        <div
          v-if="pluginProgress && pluginProgress.visible"
          class="overlay-progress overlay-progress--plugin"
          :class="{
            'overlay-progress--error': pluginProgress.status === 'failed',
          }"
        >
          <div class="overlay-progress-title">
            {{ pluginProgress.message }}
          </div>
          <div class="overlay-progress-bar">
            <div
              class="overlay-progress-fill"
              :style="{ width: `${pluginProgressPercent}%` }"
            ></div>
          </div>
          <div class="overlay-progress-meta">
            {{ pluginProgress.current || 0 }} / {{ pluginProgress.total || 0 }}
          </div>
        </div>
        <div
          ref="overlayCanvasRef"
          class="overlay-canvas"
          :class="{
            'overlay-canvas--idle': fullscreenIdle,
            'overlay-canvas--edge-tab': edgeTabShown,
          }"
          tabindex="0"
          aria-label="Image - right-click or press the menu key for actions"
          @mousemove="onStagePointerMove"
          @mouseleave="pictureFaceIdx = null"
          @touchstart="onTouchStart"
          @touchmove="onTouchMove"
          @touchend="onTouchEnd"
          @dblclick="onCanvasDblClick"
          @wheel.prevent="onCanvasWheel"
          @contextmenu="handleMediaContextMenu"
        >
          <div
            class="overlay-media"
            :style="mediaTransformStyle"
            :class="{ panning: isPanning }"
            @pointerdown="onPanStart"
            @pointermove="onPanMove"
            @pointerup="onPanEnd"
            @pointercancel="onPanEnd"
            @pointerleave="onPanEnd"
          >
            <div ref="mediaInnerRef" class="overlay-media-inner">
              <template v-if="image">
                <template v-if="isSupportedVideoFile(getOverlayFormat(image))">
                  <video
                    v-if="!videoError"
                    ref="videoRef"
                    :src="videoSrc"
                    class="overlay-video"
                    controls
                    preload="metadata"
                    playsinline
                    :draggable="!isZoomed"
                    @dragstart="handleMediaDragStart"
                    @loadedmetadata="updateOverlayDims"
                    @error="handleVideoError"
                  ></video>
                  <div v-else class="overlay-video-error">
                    <v-icon size="48" color="grey-lighten-1"
                      >mdi-video-off-outline</v-icon
                    >
                    <p class="overlay-video-error-msg">
                      Your browser cannot play this video format ({{
                        getOverlayFormat(image).toUpperCase()
                      }}).
                    </p>
                    <a
                      :href="videoSrc"
                      download
                      class="overlay-video-download-btn"
                    >
                      <v-icon size="18">mdi-download</v-icon>
                      Download video
                    </a>
                  </div>
                </template>
                <template v-else>
                  <img
                    v-if="fullImageSrc && !fullImageError"
                    :key="fullImageSrc"
                    ref="imgRef"
                    :src="fullImageSrc"
                    :alt="image.description || 'Full Image'"
                    class="overlay-img"
                    :draggable="!isZoomed"
                    @dragstart="handleMediaDragStart"
                    @load="handleFullImageLoad"
                    @error="handleFullImageError"
                  />
                  <div v-else-if="fullImageError" class="overlay-image-error">
                    <v-icon size="64" color="grey-lighten-1"
                      >mdi-image-broken-variant</v-icon
                    >
                    <p class="overlay-image-error-msg">Could not load image</p>
                  </div>
                </template>
              </template>
              <!-- A box is drawn while the reader asks about what it outlines
                   (a face crop or a detected tag pointed at in Info), every
                   box while Shift is held, the detection boxes while the
                   Segmentation boxes toggle is pressed, and the face boxes
                   while one is being drawn. Out of the way while a selection
                   is being painted. -->
              <template v-if="overlayReady && !paintActive">
                <div
                  v-if="
                    boxesAll && !faceBboxes.length && !detectionBboxes.length
                  "
                  class="face-bbox-empty"
                >
                  No faces or objects found
                </div>
                <div
                  v-else-if="showDetections && !detectionBboxes.length"
                  class="face-bbox-empty"
                >
                  No objects found
                </div>
                <div
                  v-for="(face, idx) in faceBboxes"
                  v-show="boxesAll || drawMode || hoveredFaceIdx === idx"
                  :key="`face-${idx}`"
                  class="face-bbox-overlay"
                  :style="{
                    ...getOverlayBoxStyle(face.bbox, faceBoxColor(idx)),
                    borderStyle: face.character_id == null ? 'dashed' : 'solid',
                  }"
                >
                  <span class="face-bbox-label">
                    {{ face.character_name || `Face ${idx + 1}` }}
                  </span>
                </div>
                <div
                  v-for="(det, idx) in detectionBboxes"
                  v-show="
                    boxesAll ||
                    showDetections ||
                    detectionMatchesTag(det, hoveredTagLabel)
                  "
                  :key="`det-${idx}`"
                  class="face-bbox-overlay"
                  :style="getOverlayBoxStyle(det.bbox, detectionBoxColor(det))"
                >
                  <span class="face-bbox-label">
                    {{ det.label || `Object ${idx + 1}` }}
                  </span>
                </div>
              </template>
              <!-- Word boxes for the Text tab. Same layout space as the face
                   boxes (inside the transformed media), so they follow zoom
                   and pan; box coordinates are fractions of the displayed,
                   orientation-corrected picture. aria-hidden: the word buttons
                   in the sidebar are the keyboard and screen-reader path. -->
              <svg
                v-if="showTextBoxes"
                class="picture-text-boxes"
                aria-hidden="true"
                :style="wordLayerStyle(overlayDims)"
                :viewBox="`0 0 ${overlayDims.width} ${overlayDims.height}`"
              >
                <g
                  v-for="word in pictureText.words.value"
                  v-show="word.box"
                  :key="word.index"
                  class="picture-text-box"
                  :class="{
                    'picture-text-box--match': word.matched,
                    'picture-text-box--selected': selectedWordSet.has(
                      word.index,
                    ),
                  }"
                  @pointerdown.stop
                  @dblclick.stop
                  @click.stop="onTextBoxClick(word.index, $event)"
                >
                  <rect
                    v-for="part in ['halo', 'line']"
                    :key="part"
                    :class="`picture-text-box-${part}`"
                    v-bind="wordBoxRect(word.box, overlayDims)"
                  />
                  <title>{{ word.text }}</title>
                </g>
              </svg>
              <!-- The rubber-band rectangle rides INSIDE the transformed
                   media (like the face boxes), so the same layout-space math
                   is correct at every continuous zoom scale; the draw layer
                   below only owns the pointer events and the hint. -->
              <div
                v-if="drawMode && drawRectStyle"
                class="overlay-draw-rect"
                :style="drawRectStyle"
              ></div>
              <!-- The Edit tab's selection and crop frame (Paint tools). -->
              <PaintLayer
                v-if="paintActive && overlayReady"
                :paint="paint"
                :dims="overlayDims"
              />
              <!-- One picture over another, cut at the slider: the original
                   over an edit's result, or any two pictures picked. -->
              <div
                v-if="compareShown"
                class="paint-compare"
                :style="wordLayerStyle(overlayDims)"
              >
                <img
                  class="paint-compare-before"
                  :src="compare.beforeSrc"
                  alt=""
                  :style="{ clipPath: `inset(0 ${100 - comparePosition}% 0 0)` }"
                />
                <span
                  class="paint-compare-split"
                  :style="{ left: `${comparePosition}%` }"
                  aria-hidden="true"
                >
                  <v-icon size="18">mdi-compare-horizontal</v-icon>
                </span>
                <input
                  v-model.number="comparePosition"
                  class="paint-compare-range"
                  type="range"
                  min="0"
                  max="100"
                  step="1"
                  :aria-label="`Compare: how much of ${compare.beforeLabel || 'the original'} shows`"
                  @pointerdown.stop.prevent="onCompareStart"
                  @pointermove="onCompareMove"
                  @pointerup="onCompareEnd"
                  @pointercancel="onCompareEnd"
                  @dblclick.stop
                  @touchstart.stop
                  @touchmove.stop
                  @touchend.stop
                  @keydown.esc.stop.prevent="closeCompare"
                />
                <!-- Each side's name is also the way out onto that side. -->
                <button
                  v-for="side in COMPARE_SIDES"
                  :key="side"
                  type="button"
                  class="paint-compare-tag"
                  :class="`paint-compare-tag--${side}`"
                  @click.stop="leaveCompare(side)"
                  @pointerdown.stop
                  @dblclick.stop
                >
                  <Tooltip
                    :text="`Leave the comparison on ${compareLabel(side)}`"
                    activator="parent"
                    :describe="false"
                  />
                  <v-icon v-if="side === 'before'" size="14">mdi-chevron-left</v-icon>
                  {{ compareLabel(side) }}
                  <v-icon v-if="side === 'after'" size="14">mdi-chevron-right</v-icon>
                </button>
              </div>
            </div>
          </div>

          <!-- The way out of a comparison, where it cannot be zoomed or
               panned away: the bottom of the screen, glowing its own teal. -->
          <div
            v-if="compareShown"
            class="compare-exit"
            role="group"
            aria-label="Leave the comparison"
            @pointerdown.stop
            @dblclick.stop
          >
            <button
              type="button"
              class="compare-exit-side compare-exit-side--before"
              @click.stop="leaveCompare('before')"
            >
              <v-icon size="18">mdi-chevron-left</v-icon>
              {{ compareLabel("before") }}
            </button>
            <button
              type="button"
              class="compare-exit-main"
              aria-keyshortcuts="Escape"
              @click.stop="leaveCompare('after')"
            >
              Exit comparison
              <kbd class="compare-exit-key" aria-hidden="true">Esc</kbd>
            </button>
            <button
              type="button"
              class="compare-exit-side compare-exit-side--after"
              @click.stop="leaveCompare('after')"
            >
              {{ compareLabel("after") }}
              <v-icon size="18">mdi-chevron-right</v-icon>
            </button>
          </div>

          <!-- Takes the pointer while a Paint tool is in hand; with none held
               the lightbox's own pan has it. A stroke's click and
               touches stop here: on the canvas they hide the chrome (the
               toolbox with it) or read as a swipe to the next picture. -->
          <div
            v-if="
              paintActive &&
              overlayReady &&
              !compareShown &&
              paint.tool.value
            "
            class="paint-input-layer"
            :class="`paint-input-layer--${paint.tool.value}`"
            @pointerdown.prevent="onPaintStart"
            @pointermove.prevent="onPaintMove"
            @pointerup.prevent="onPaintEnd"
            @pointercancel.prevent="onPaintCancel"
            @click.stop
            @dblclick.stop
            @touchstart.stop
            @touchmove.stop
            @touchend.stop
          ></div>

          <div
            v-if="drawMode"
            class="overlay-draw-layer"
            @pointerdown.prevent="onDrawStart"
            @pointermove.prevent="onDrawMove"
            @pointerup.prevent="onDrawEnd"
            @pointercancel.prevent="onDrawCancel"
            @pointerleave.prevent="onDrawCancel"
          >
            <div class="overlay-draw-hint">
              <span>
                Draw a bounding box to create the {{ drawModeLabel }}
              </span>
              <button
                class="overlay-draw-cancel"
                type="button"
                @click.stop="clearDrawMode"
              >
                Cancel
              </button>
            </div>
          </div>

          <button
            class="overlay-nav overlay-nav-left"
            :class="{ hidden: chromeHidden }"
            @click.stop="showPrevImage"
            @dblclick.stop
            aria-label="Previous (←)"
          >
            <Tooltip text="Previous (←)" activator="parent" :describe="false" />
            <v-icon>mdi-chevron-left</v-icon>
          </button>
          <button
            class="overlay-nav overlay-nav-right"
            :class="{ hidden: chromeHidden }"
            @click.stop="showNextImage"
            @dblclick.stop
            aria-label="Next (→)"
          >
            <Tooltip text="Next (→)" activator="parent" :describe="false" />
            <v-icon>mdi-chevron-right</v-icon>
          </button>

          <!-- The zoom announcer: the button's aria-label carries the live
               value (no aria-live on the button); this hidden status node
               announces on settle - 500 ms after the last wheel change, and
               immediately on a snap stop. The settle timer lives in
               useWheelZoom. -->
          <span class="visually-hidden" role="status" aria-live="polite">{{
            zoomAnnouncement
          }}</span>

          <!-- Both hints stand down while a receipt is up: they are ambient
               teaching with no deadline, the receipt is feedback on a committed
               action that expires. "Click or Space to show controls" under an
               Undo button is worse than redundant, because that click is not
               the click the user wants. They return when the receipt retires. -->
          <div
            v-if="swipeHintVisible && !hasReceipt"
            class="overlay-swipe-hint"
          >
            <v-icon size="18">mdi-swap-horizontal</v-icon>
            <span>Swipe to navigate</span>
          </div>
          <div v-if="chromeHidden && !hasReceipt" class="overlay-chrome-hint">
            <span>Click or <kbd>Space</kbd> to show controls</span>
          </div>
        </div>

        <!-- The closed inspector's edge tab (the app's standard handle),
             naming the tab it reopens on. With every tool in the inspector,
             closing it is the everyday focus view. In full screen the
             pointer at the right edge brings it out. -->
        <InspectorEdgeTab
          v-if="edgeTabShown"
          :label="edgeTabLabel"
          :nudge="edgeTabNudge"
          :name="`Show picture details: ${edgeTabLabel}`"
          @open="openInspectorFromEdge"
        />
        <!-- Said once on entering full screen, then gone for the visit. -->
        <div
          v-if="fullscreenNotice && !fullscreenBarShown"
          class="overlay-fullscreen-notice"
          role="status"
        >
          Point at the top for controls ·
          <kbd>Esc</kbd> exits full screen
        </div>

        <!-- The tools themselves are in the Edit tab; the options of the one
             in hand that act on the picture (combine mode, tolerance, crop
             framings) float above it. Brush size is in the tab too. -->
        <PaintOptionsBar
          v-if="paintActive && !chromeHidden && paintOptionsShown"
          :paint="paint"
          :framings="cropFramingsHere"
          @apply-crop="applyCrop"
        />

        <!-- Right-click on a filmstrip thumb: Compare, greyed with its reason
             until two are picked. -->
        <Teleport to="body">
          <div
            v-if="filmstripMenu"
            ref="filmstripMenuRef"
            class="filmstrip-ctx-menu ctx-menu ctx-menu--on-dark"
            :style="{ left: `${filmstripMenu.x}px`, top: `${filmstripMenu.y}px` }"
            role="menu"
            tabindex="-1"
          >
            <button
              class="ctx-item"
              role="menuitem"
              type="button"
              :disabled="!!filmstripCompareReason"
              @click="compareFromFilmstripMenu"
            >
              <Tooltip
                :text="
                  filmstripCompareReason ||
                  'Lay one picture over the other, cut at a slider'
                "
                activator="parent"
              />
              <v-icon class="ctx-icon">mdi-compare-horizontal</v-icon>
              Compare
              <span v-if="filmstripCompareReason" class="ctx-meta">{{
                COMPARE_NOTES[filmstripCompareReason] || "Ctrl-click another"
              }}</span>
            </button>
          </div>
        </Teleport>

        <!-- The lightbox's own narration of an undoable action. Last child of
             `.overlay-main` on purpose: last-in-DOM is last-in-tab-order, and
             it escapes `.overlay-canvas`'s `overflow: hidden`, which would clip
             the focus ring on the Undo button. -->
        <OverlayActionReceipt
          v-if="!isReadOnly"
          ref="receiptRef"
        />
      </div>
      <OverlayFilmstrip
        ref="filmstripRef"
        :items="filmstripCanvasWindow"
        :offsets="filmstripLayout.offsets"
        :widths="filmstripLayout.widths"
        :hidden="chromeHidden"
        :read-only="isReadOnly"
        :picked-ids="filmstripPicks"
        @select="selectImageByIndex"
        @pick="onFilmstripPick"
        @context-menu="onFilmstripContextMenu"
        @toggle-expand="toggleFilmstripStackExpand"
        @prefetch="prefetchFilmstripStackMembers"
        @visible-range="filmstripVisible = $event"
        @stack-move="(item, where) => moveStackMember(item.id, where)"
      />
      </div>
      <!-- The shared inspector (shell contract rule 8), in its lightbox
           variant: dark in both themes. It stands where the right rail stands,
           with its own open flag (default open), not the rail's. -->
      <AppInspector
        v-model="sidebarTab"
        class="overlay-sidebar"
        label="Picture details"
        lightbox
        :open="inspectorShown"
        :tabs="sidebarTabs"
      >
        <!-- `v-show`, never `v-if`: the panels hold editing state and the
             refs the overlay reaches into for its keyboard shortcuts (T
             opens the tag field, Escape cancels an edit), and unmounting the
             Info group behind the Recipe tab would take those with it. -->
        <div v-show="sidebarTab === 'info'" class="overlay-sidebar-group">
          <!-- The picture named by its description's first sentence, then
               its rating and heart: the head of the grid rail's one-picture
               view. -->
          <div v-if="image" class="overlay-info-head">
            <!-- No tip: the whole description is a section further down. -->
            <div v-if="pictureName" class="overlay-info-name">
              {{ pictureName }}
            </div>
            <StarRatingOverlay
              class="overlay-rail-rating"
              :score="isReadOnly ? guestScore || 0 : image?.score || 0"
              :readonly="!isReadOnly && isCurrentLocked"
              icon-size="18"
              :favouritable="!isReadOnly && favouritable"
              :favourite="Boolean(image?.favourite)"
              @set-score="setScore"
              @toggle-favourite="toggleFavourite"
            />
          </div>

          <!-- The faces as named crops (the grid rail's one-picture order).
               Pointing at a crop draws that face's box on the picture; pointing
               at the face on the picture rings its crop here. The pencil opens
               the person menus for all of them at once. -->
          <div class="sidebar-section sidebar-section--faces">
            <SectionHeader
              v-model:collapsed="facesCollapsed"
              label="Faces"
              :count="faceAssignItems.length || null"
              on-dark
            >
              <template v-if="image && !isReadOnly" #actions>
                <button
                  v-if="faceAssignItems.length || !isMobile"
                  ref="facesPencilRef"
                  class="section-meta-btn"
                  :class="{ 'section-meta-btn--on': namingFaces }"
                  type="button"
                  aria-label="Edit faces"
                  aria-haspopup="dialog"
                  aria-controls="overlay-faces-panel"
                  :aria-expanded="namingFaces ? 'true' : 'false'"
                  @click.stop="namingFaces = !namingFaces"
                >
                  <Tooltip text="Edit faces" activator="parent" :describe="false" />
                  <v-icon size="16">mdi-pencil-outline</v-icon>
                </button>
              </template>
            </SectionHeader>
            <template v-if="!facesCollapsed">
              <div v-if="faceAssignItems.length" class="face-chips">
                <div
                  v-for="face in faceAssignItems"
                  :key="face.faceKey"
                  class="face-chip"
                  :class="{
                    'face-chip--unnamed': face.character_id == null,
                    'face-chip--ringed': pictureFaceIdx === face.faceIdx,
                  }"
                  :style="{ '--face-hue': faceBoxColor(face.faceIdx) }"
                  @mouseenter="hoveredFaceIdx = face.faceIdx"
                  @mouseleave="hoveredFaceIdx = null"
                >
                  <div class="face-chip-crop">
                    <div
                      class="face-assign-crop"
                      :style="getFaceThumbStyle(face, face.faceIdx, FACE_CHIP_SIZE)"
                    ></div>
                  </div>
                  <span class="face-chip-name">{{
                    faceAssignedName(face) || "Unknown"
                  }}</span>
                </div>
              </div>
              <div v-if="!faceAssignItems.length" class="face-assign-empty">
                No faces detected
              </div>
            </template>
            <!-- The pencil's panel: a person picker per face, over the
                 sidebar rather than pushing Tags down. -->
            <FacesPanel
              v-if="namingFaces && image && !isReadOnly && !facesCollapsed"
              id="overlay-faces-panel"
              ref="facesPanelRef"
              class="overlay-faces-panel"
              on-dark
              allow-create
              :rows="facePanelRows"
              :anchor="facesPencilRef"
              @close="closeFacesPanel"
              @hover="(row) => (hoveredFaceIdx = row ? row.face.faceIdx : null)"
              @assign="(row, payload) => handleFaceAssign(row.face, payload)"
              @unassign="(row) => unassignFaceCharacter(row.face)"
              @create="(row, query) => openCreatePersonForFace(row.face, query)"
            >
              <template v-if="!isMobile" #footer>
                <AppButton
                  size="sm"
                  on-dark
                  icon-left="vector-square-plus"
                  @click="drawFaceFromPanel"
                >
                  Draw a face box
                </AppButton>
              </template>
            </FacesPanel>
          </div>

          <OverlayTagsPanel
            ref="tagsPanelRef"
            :image="image"
            :hidden-tags="hiddenTags"
            :apply-tag-filter="applyTagFilter"
            :locked="isCurrentLocked"
            :lock-note="currentLockReason"
            :boxed-tags="detectionLabels"
            @hover-tag="(label) => (hoveredTagLabel = label)"
            @update-tags="handleTagsUpdate"
            @overlay-change="(payload) => emit('overlay-change', payload)"
            @add-tag="(imageId, tag) => emit('add-tag', imageId, tag)"
            @request-metadata-refresh="fetchOverlayMetadata"
          />

          <OverlayDescriptionPanel
            ref="descriptionPanelRef"
            :image="image"
            :locked="isCurrentLocked"
            :lock-note="currentLockReason"
            :text-state="pictureText.text.value.state"
            :text-lines="pictureText.text.value.lines"
            :active-tab="pictureText.activeTab.value"
            :selected-words="pictureText.selectedWords.value"
            :text-read-busy="pictureText.readAgainBusy.value"
            @update-description="handleDescriptionUpdate"
            @editing-finished="focusOverlayCanvas"
            @pick-tab="pictureText.pickTab"
            @select-word="pictureText.selectWord"
            @clear-word-selection="pictureText.clearSelection"
            @read-text-again="pictureText.readAgain"
          />

          <OverlayMetadataPanel
            :image="image"
            :date-format="dateFormat"
            :video-duration="videoMeta.duration"
          />
        </div>

        <!-- `v-if`, unlike the Info group above: the Recipe tab is read-only
             and holds nothing worth preserving across a tab switch, so it
             costs nothing to build on demand and nothing to throw away. -->
        <OverlayRecipePanel
          v-if="sidebarTab === 'recipe'"
          :recipe="comfyMetadata"
          :picture-id="image?.id ?? null"
          :can-generate-variants="
            comfyuiConfigured && !isReadOnly && !!image?.id
          "
          :comfyui-configured="comfyuiConfigured"
          @run="emit('run-recipe', image?.id)"
          @use-as-input="emit('use-as-input', image?.id)"
        />

        <!-- `v-show` inside a `v-if`: the typed instruction, the chosen
             workflow and a run in flight survive a trip to Info and a step
             over a video, where the tab itself is absent. -->
        <OverlayEditPanel
          v-if="editTabAvailable"
          v-show="sidebarTab === 'edit'"
          :picture-id="image?.id ?? null"
          :active="sidebarTab === 'edit'"
          :comfyui-progress="comfyuiProgress"
          :comfyui-progress-percent="comfyuiProgressPercent"
          :paint="paint"
          :apply-to-ids="applyToIds"
          :comfyui-configured="comfyuiConfigured"
          @show-picture="showEditResult"
          @running="(on) => (editRunning = on)"
          @more-options="(payload) => emit('edit-more-options', payload)"
        >
          <!-- Quick changes: what changes this picture without a workflow
               (rotate, crop, filters) and Upscale. Rotate is one 90° step,
               immediately, with Undo on the receipt; greyed with the reason
               when the file cannot carry a turn every viewer agrees on. -->
          <template #quick-changes>
            <div class="overlay-quick">
              <div class="section-label section-label--on-dark">
                Quick changes
              </div>
              <div class="overlay-quick-actions">
                <AppButton
                  size="sm"
                  variant="ghost"
                  on-dark
                  icon-left="rotate-left"
                  :tooltip="rotateLeftTitle"
                  aria-keyshortcuts="["
                  :aria-disabled="canRotateCurrent ? undefined : 'true'"
                  :aria-label="
                    canRotateCurrent ? undefined : `Rotate left: ${rotateLeftTitle}`
                  "
                  @click.stop="rotateCurrentImage(ROTATE_CCW)"
                >
                  Rotate left
                </AppButton>
                <AppButton
                  size="sm"
                  variant="ghost"
                  on-dark
                  icon-left="rotate-right"
                  :tooltip="rotateRightTitle"
                  aria-keyshortcuts="]"
                  :aria-disabled="canRotateCurrent ? undefined : 'true'"
                  :aria-label="
                    canRotateCurrent ? undefined : `Rotate right: ${rotateRightTitle}`
                  "
                  @click.stop="rotateCurrentImage(ROTATE_CW)"
                >
                  Rotate right
                </AppButton>
                <AppButton
                  size="sm"
                  variant="ghost"
                  on-dark
                  icon-left="crop"
                  tooltip="Crop to a new picture (C)"
                  aria-keyshortcuts="C"
                  :aria-pressed="paint.tool.value === 'crop' ? 'true' : 'false'"
                  @click.stop="
                    paint.setTool(paint.tool.value === 'crop' ? null : 'crop')
                  "
                >
                  Crop
                </AppButton>
                <!-- Upscale… (#1868): this picture, by the factor and model
                     the Run popup's added upscale remembers, stacked on top
                     of it. -->
                <AppButton
                  v-if="canUpscaleCurrent"
                  size="sm"
                  variant="ghost"
                  on-dark
                  icon-left="arrow-expand-all"
                  data-testid="overlay-upscale"
                  :tooltip="upscaleTitle"
                  @click.stop="upscale.openFor([image.id])"
                >
                  Upscale…
                </AppButton>
                <v-menu
                  v-model="pluginMenuOpen"
                  :close-on-content-click="false"
                  location-strategy="connected"
                  location="bottom end"
                  origin="top end"
                  transition="scale-transition"
                >
                  <template #activator="{ props: filterMenuProps }">
                    <AppButton
                      v-bind="filterMenuProps"
                      class="overlay-comfy-activator"
                      size="sm"
                      variant="ghost"
                      on-dark
                      icon-left="tune-variant"
                    >
                      Filters…
                    </AppButton>
                  </template>
                  <div class="overlay-comfy-panel">
                    <div class="overlay-comfy-header">Filters</div>
                    <div class="overlay-comfy-body">
                      <div
                        v-if="!overlayPluginOptions.length"
                        class="overlay-comfy-warning"
                      >
                        No filters available.
                      </div>
                      <template v-else>
                        <label class="overlay-comfy-field-label">Filters</label>
                        <select
                          v-model="overlaySelectedPluginName"
                          class="overlay-comfy-select"
                        >
                          <option
                            v-for="plugin in overlayPluginOptions"
                            :key="plugin.name"
                            :value="plugin.name"
                          >
                            {{ plugin.display_name || plugin.name }}
                          </option>
                        </select>
                        <PluginParametersUI
                          v-model="overlayPluginParameters"
                          :plugin="activeOverlayPluginSchema"
                          :show-description="true"
                          tone="dark"
                          input-class="overlay-comfy-select"
                          label-class="overlay-comfy-field-label"
                        />
                        <label class="overlay-comfy-checkbox-row">
                          <input v-model="stackFilterOutputs" type="checkbox" />
                          <span>Stack new images with the originals</span>
                        </label>
                        <div class="overlay-comfy-actions">
                          <button
                            class="overlay-comfy-run"
                            type="button"
                            :disabled="!image || !overlaySelectedPluginName"
                            @click.stop="runOverlayPlugin"
                          >
                            <v-icon size="16">mdi-play</v-icon>
                            <span>Run</span>
                          </button>
                        </div>
                      </template>
                    </div>
                  </div>
                </v-menu>
              </div>
            </div>
          </template>
        </OverlayEditPanel>
      </AppInspector>
    </div>

    <!-- New person from a face row (#645). Overlay-hosted: the flow's state
         (the target face, the select to return focus to) is overlay-local, and
         living inside the `v-if="open"` root means it cannot outlive the
         lightbox. `v-dialog` teleports the dialog itself to <body>, so nesting
         here costs no layout or stacking context. -->
    <CharacterEditor
      :open="createPersonOpen"
      :character="createPersonCharacter"
      :projects="createPersonProjects"
      @close="handleCreatePersonClose"
      @saved="handleCreatePersonSaved"
    />
    <OverlaySaveAsDialog
      v-if="fallbackSaveDialog.open"
      :open="fallbackSaveDialog.open"
      :suggested-name="fallbackSaveDialog.suggestedName"
      :original-extension="fallbackSaveDialog.originalExtension"
      :media-noun="fallbackSaveDialog.mediaNoun"
      @close="closeFallbackSaveDialog"
      @save="confirmFallbackSaveDialog"
    />
  </div>
  </Teleport>
</template>

<script setup>
import { withRef } from "../../utils/withRef.js";
import {
  onMounted,
  onUnmounted,
  ref,
  computed,
  nextTick,
  toRefs,
  watch,
} from "vue";
import { useWheelZoom } from "../../composables/useWheelZoom";
import {
  TEXT_TAB,
  usePictureText,
  wordBoxRect,
  wordLayerStyle,
} from "../../composables/usePictureText";
import {
  isSupportedVideoFile,
  getOverlayFormat,
  buildMediaUrl,
  MediaFormat,
  mediaMimeType,
  safeDownloadName,
  setInternalDragPayload,
  dedupeDetections,
  displayedAspectRatio,
} from "../../utils/media.js";
import {
  API_BASE_URL,
  appendShareToken,
  isReadOnly,
} from "../../utils/apiClient";
import {
  getPictureMetadata,
  listPictureFaces,
  listPicturesByIds,
  listPictureDetections,
  addPictureFace,
  downloadPicture,
  rotatePictures,
} from "../../api/pictures";
import {
  listCharacters,
  getCharacterName,
  getCharacterThumbnail,
  addCharacterFacesByFaceId,
  removeCharacterFacesByFaceId,
} from "../../api/characters";
import { listStackPictures, setStackOrder } from "../../api/stacks";
import { getPictureRecipe } from "../../api/comfyui";
import { listProjects } from "../../api/projects";
import { useGenStackPrefsStore } from "../../stores/useGenStackPrefsStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useUpscaleStore } from "../../stores/useUpscaleStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useOperationStore } from "../../stores/useOperationStore";
import { useProjectStore } from "../../stores/useProjectStore";
import { useSearchStore } from "../../stores/useSearchStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import {
  normalizeWheelDelta,
  ZOOM_EXIT_GESTURE_GAP_MS,
} from "../../utils/zoomMath";
import { nextFreeCharacterName } from "../../utils/characterCreateFlow.js";
import { bareDigit } from "../../utils/shortcutKeys";
import AddToEntityControl from "../widgets/AddToEntityControl.vue";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import FacesPanel from "../widgets/FacesPanel.vue";
import AppInspector from "../widgets/AppInspector.vue";
import InspectorEdgeTab from "../widgets/InspectorEdgeTab.vue";
import CharacterEditor from "../editors/CharacterEditor.vue";
import OverlayDescriptionPanel from "./OverlayDescriptionPanel.vue";
import OverlayFilmstrip from "./OverlayFilmstrip.vue";
import OverlayMetadataPanel from "./OverlayMetadataPanel.vue";
import OverlayRecipePanel from "./OverlayRecipePanel.vue";
import OverlayEditPanel from "./OverlayEditPanel.vue";
import PaintLayer from "./PaintLayer.vue";
import PaintOptionsBar from "./PaintOptionsBar.vue";
import TbGlobalActions from "../panels/TbGlobalActions.vue";
import {
  MODE_TOOLS,
  PAINT_TOOLS,
  SIZED_TOOLS,
  usePaintSelection,
} from "../../composables/usePaintSelection";
import { cropFramings } from "../../utils/cropFramings";
import OverlayTagsPanel from "./OverlayTagsPanel.vue";
import OverlayActionReceipt from "../widgets/OverlayActionReceipt.vue";
import OverlaySaveAsDialog from "../widgets/OverlaySaveAsDialog.vue";
import PluginParametersUI from "../widgets/PluginParametersUI.vue";
import StarRatingOverlay from "../widgets/StarRatingOverlay.vue";
import SectionHeader from "../widgets/SectionHeader.vue";
import { useSectionCollapse } from "../../composables/useSectionCollapse";
import { FAVOURITE_KEY } from "../../stores/useFavouritesStore";
import Tooltip from "../widgets/Tooltip.vue";
import {
  applyStackBackgroundAlpha,
  faceBoxColor,
  getStackColor,
  getStackColorIndexFromId,
  toggleScore,
} from "../../utils/utils.js";
import {
  isEditableElement,
  isTypingTarget,
  LIGHTBOX_HOST_ID,
} from "../../utils/dom.js";
import { COMPARE_NOTES, compareBlockReason } from "../../utils/compare";
import {
  getPictureStackId,
  getStackPositionValue,
  moveStackMemberInOrder,
  sortStackMembers,
} from "../../utils/stack.js";
import { dedupeTagList, getTagList } from "../../utils/tags.js";
import {
  ROTATE_CCW,
  ROTATE_CW,
  ROTATE_OP_TYPE,
  canRotateInPlace,
  rotateBlockReason,
  rotateSkipNote,
} from "../../utils/rotate.js";
import { errorDetail } from "../../utils/apiError";

// Failures report through the notice surface instead of a blocking native
// alert() (docs/design/notice-surface.md §1).
const noticeStore = useNoticeStore();
const runDialogStore = useRunDialogStore();
// Undo/redo is the same stack the grid uses; only the narration differs here
// (OverlayActionReceipt, in the lightbox's own dark chrome).
const operationStore = useOperationStore();
const receiptRef = ref(null);
const fallbackSaveDialog = ref({
  open: false,
  suggestedName: "",
  originalExtension: "",
  mediaNoun: "picture",
});
let fallbackSaveResolver = null;

function closeFallbackSaveDialog() {
  fallbackSaveDialog.value = { ...fallbackSaveDialog.value, open: false };
  const resolve = fallbackSaveResolver;
  fallbackSaveResolver = null;
  resolve?.(null);
}

function confirmFallbackSaveDialog(filename) {
  fallbackSaveDialog.value = { ...fallbackSaveDialog.value, open: false };
  const resolve = fallbackSaveResolver;
  fallbackSaveResolver = null;
  resolve?.(filename);
}

function requestFallbackSaveFilename(info) {
  closeFallbackSaveDialog();
  fallbackSaveDialog.value = {
    open: true,
    suggestedName: info.filename,
    // Keep the suffix the user already recognises from the original filename.
    // The stored media format can be an equivalent alias (jpg vs jpeg), but
    // Save As should never rewrite the visible default name behind their back.
    originalExtension: MediaFormat(info.filename) || info.format,
    mediaNoun: info.noun,
  };
  return new Promise((resolve) => {
    fallbackSaveResolver = resolve;
  });
}
/** A receipt is on screen: the two bottom-centre hints stand down while it is. */
const hasReceipt = computed(() => Boolean(operationStore.receipt));

const props = defineProps({
  open: { type: Boolean, default: false },
  initialImageId: { type: [String, Number, null], default: null },
  initialExpandedStackIds: { type: Array, default: () => [] },
  allImages: { type: Array, default: () => [] },
  backendUrl: { type: String, default: () => API_BASE_URL },
  tagUpdate: { type: Object, default: () => ({}) },
  descriptionUpdate: { type: Object, default: () => ({}) },
  smartScoreUpdate: { type: Object, default: () => ({}) },
  detectionUpdate: { type: Object, default: () => ({}) },
  // Signals the OCR text of the named pictures changed (`ocr_text`).
  textUpdate: { type: Object, default: () => ({}) },
  // Signals the named pictures were turned (`orientation`) - a rotate, or the
  // undo/redo of one, from this tab or another.
  orientationUpdate: { type: Object, default: () => ({}) },
  hiddenTags: { type: Array, default: () => [] },
  applyTagFilter: { type: Boolean, default: false },
  dateFormat: { type: String, default: "locale" },
  showStacks: { type: Boolean, default: true },
  showProblemIcon: { type: Boolean, default: true },
  // False from the Scrapheap, which shows no heart: click and H both go inert.
  favouritable: { type: Boolean, default: true },
  availablePlugins: { type: Array, default: () => [] },
  comfyuiProgress: { type: Object, default: null },
  comfyuiProgressPercent: { type: Number, default: 0 },
  pluginProgress: { type: Object, default: null },
  pluginProgressPercent: { type: Number, default: 0 },
  comfyuiConfigured: { type: Boolean, default: false },
  guestScore: { type: Number, default: 0 },
  // The grid's *Edit…* (`{pictureIds}`, a new object per gesture): open on
  // the Edit tab, with *Apply to* offering every picture named.
  editRequest: { type: Object, default: null },
  // The grid's *Compare* (`{beforeId, afterId}`, a new object per gesture):
  // the first picture laid over the second, which the lightbox opens on.
  compareRequest: { type: Object, default: null },
  // The view the picture was opened from ("All Pictures", a set's name): the
  // toolbar's way back names where it goes.
  viewLabel: { type: String, default: "Back" },
});

const {
  open,
  initialImageId,
  initialExpandedStackIds,
  allImages,
  backendUrl,
  tagUpdate,
  descriptionUpdate,
  smartScoreUpdate,
  detectionUpdate,
  textUpdate,
  orientationUpdate,
  hiddenTags,
  applyTagFilter,
  showStacks,
  showProblemIcon,
  availablePlugins,
  comfyuiProgress,
  comfyuiProgressPercent,
  pluginProgress,
  pluginProgressPercent,
  comfyuiConfigured,
  guestScore,
  editRequest,
  compareRequest,
} = toRefs(props);

const image = ref(null);
const searchStore = useSearchStore();
// Text in the picture (#1197). The sidebar's Text tab and the word boxes over
// the picture share its tab and selection.
const pictureText = usePictureText({
  // Null while closed, so reopening lands afresh (the search may have moved).
  pictureId: computed(() => (open.value ? (image.value?.id ?? null) : null)),
  getSearchQuery: () => searchStore.searchQuery || "",
});
// Hidden while a "Read again" runs: the old boxes are about to be replaced.
const showTextBoxes = computed(
  () =>
    pictureText.activeTab.value === TEXT_TAB &&
    !pictureText.readAgainBusy.value &&
    overlayReady.value,
);
const selectedWordSet = computed(
  () => new Set(pictureText.selectedWords.value),
);
// Grouping (project/set) membership is stack-atomic: a single stack member shown
// in the overlay cannot have its membership changed individually - the user must
// unstack first. Whole-stack edits happen from the collapsed grid tile.
const stackGroupingLockReason = computed(() => {
  if (!image.value) return null;
  const count = Number(image.value.stackCount ?? image.value.stack_count ?? 0);
  return count > 1
    ? "Unstack first to change the project or set of an individual stack picture."
    : null;
});
const addToSetControlRef = ref(null);
const tagsPanelRef = ref(null);
const isAddingTag = computed(() => tagsPanelRef.value?.addingTag ?? false);

function handleTagsUpdate(newTagsArray) {
  if (image.value) {
    image.value = { ...image.value, tags: newTagsArray };
  }
}
// The pane stands where the right rail stands (#2163) but keeps its own open
// flag, default open: a reader who keeps the grid's stats rail shut still
// wants a picture's details beside it. A shortcut that needs the pane (T, the
// grid's *Edit…*) opens it for the moment (`openSidebar`), without persisting
// the reader's choice; S and the toolbar toggle persist it.
const sidebarStore = useSidebarStore();
const sidebarOpen = computed(() => sidebarStore.lightboxRailOpen);
// In full screen the inspector is gone until asked for (I, or the edge tab),
// and asking does not touch the rail's own remembered flag. (Full screen
// itself is set up with the chrome below.)
const fullscreen = ref(false);
const fullscreenInspector = ref(false);
const inspectorShown = computed(() =>
  fullscreen.value ? fullscreenInspector.value : sidebarOpen.value,
);
function openSidebar() {
  if (fullscreen.value) fullscreenInspector.value = true;
  else sidebarStore.lightboxRailOpen = true;
}

// ── Info | Recipe (#1313) ──────────────────────────────────────────────────
//
// The v1.12 design puts the sidebar's two subjects on the inspector's own tab
// band rather than stacking a Recipe section under Tags: Info is everything the
// pane showed before, Recipe is everything about how the picture was made.
//
// The choice is remembered as the lightbox walks the filmstrip: somebody
// reading recipes wants the next picture's recipe, not its description.
//
// **Never disabled - absent instead, on a picture that has no recipe at all.**
// A disabled tab is a dead control: a disabled button fires no pointer events,
// so the tooltip explaining why would never open for a mouse. The design gives
// an A1111 picture "the same Recipe tab" and it still gets one, because it HAS
// a recipe; a holiday photo has nothing behind the tab but a sentence saying
// so, and a tab whose only content is its own denial is not worth a tab.
const chosenSidebarTab = ref("info");
// Held across the read rather than cleared with the recipe: clearing it would
// take the tab away for the length of one file read on every filmstrip step
// and put it back, under the reader's cursor.
const recipeTabShown = ref(false);
// ── Edit (#1381) ───────────────────────────────────────────────────────────
//
// Offered whenever the session may write, whether or not the picture has a
// recipe: a holiday photo gets Edit too. It holds the picture's Quick changes
// (rotate, crop, filters) as well as the edit workflows, so a machine without
// a ComfyUI gets the tab with only those. Absent rather than disabled when it
// cannot be used, by the Recipe tab's rule, and absent on a video (the
// design's answer: no edit workflow takes one yet).
//
// The PANEL hangs off `editTabAvailable` alone, so stepping over a video hides
// the tab without unmounting the typed instruction or a run in flight.
const editTabAvailable = computed(() => !isReadOnly.value);
const editTabShown = computed(
  () =>
    editTabAvailable.value &&
    !!image.value?.id &&
    !isSupportedVideoFile(getOverlayFormat(image.value)),
);
// Upscale… on this picture (#1868): where the Edit tab can run, a picture.
const upscale = useUpscaleStore();
const canUpscaleCurrent = computed(
  () => editTabShown.value && comfyuiConfigured.value,
);
const upscaleTitle = computed(() => {
  // Before the shelf is read the model is not known: the verb alone.
  if (upscale.models === null) return "Upscale…";
  const how = upscale.model?.name || "a Lanczos resize";
  return `Upscale ${upscale.factor}× with ${how}`;
});
// Whether a run the Edit tab started is still going. While that tab is on
// screen its own bar reports the run, so the lightbox's ComfyUI bar would be a
// second copy. A run from the grid's menus still gets the lightbox bar.
const editRunning = ref(false);
const editTabShowsRun = computed(
  () => editRunning.value && inspectorShown.value && sidebarTab.value === "edit",
);
// ── Tasks ──────────────────────────────────────────────────────────────────
//
// No Tasks tab here: the task list is the title bar's pill (#2163). A deep
// link to the Tasks tab (a notice's *Show*, `showTasksTab`) would otherwise
// select a tab in the stats rail under the lightbox, so it opens the pill.
watch(
  () => sidebarStore.tasksTabRequest,
  () => {
    if (open.value) sidebarStore.tasksPillRequest += 1;
  },
);
// The reader's CHOICE is remembered even while the tab it names is gone, so
// stepping over a photo in a run of ComfyUI pictures does not silently move
// them to Info for the rest of the walk.
const sidebarTab = computed({
  get: () => {
    const chosen = chosenSidebarTab.value;
    if (chosen === "recipe" && !recipeTabShown.value) return "info";
    if (chosen === "edit" && !editTabShown.value) return "info";
    return chosen;
  },
  set: (value) => {
    chosenSidebarTab.value = value;
  },
});
// Empty, not one tab: a lone Info button would be a control with nothing to
// switch to - a dead tab by another name, which is the thing hiding Recipe was
// meant to avoid. The band itself stays, for the rating at its end.
const sidebarTabs = computed(() => {
  const tabs = [
    { value: "info", label: "Info", icon: "mdi-information-outline" },
  ];
  if (recipeTabShown.value) {
    tabs.push({ value: "recipe", label: "Recipe", icon: "mdi-bookmark-outline" });
  }
  if (editTabShown.value) {
    // `mdi-sitemap-outline` is the app's ComfyUI workflow glyph everywhere
    // else (the Run popup, *Use as input for…*, the Workflows view).
    tabs.push({ value: "edit", label: "Edit", icon: "mdi-sitemap-outline" });
  }
  return tabs.length > 1 ? tabs : [];
});

// **The band taking focus with it when it goes.** Arrow keys are a window
// listener, so a reader can step the filmstrip with focus sitting on a tab
// button; the next picture having no recipe then unmounts the button under
// them and focus falls to `document.body`, where the ring is gone and the
// shortcuts that check for an editable target keep working - so nothing says
// it happened. It catches Info as well as Recipe, which means a reader who
// never opened Recipe loses focus the same way. The canvas is where the
// receipt's Escape path already sends it.
// Edit coming and going removes tab buttons the same way, so the rule is on the
// band as a whole: whenever the tab set changes and the focused tab button did
// not survive it.
watch(
  () => sidebarTabs.value.map((tab) => tab.value).join(","),
  () => {
    const active = document.activeElement;
    if (!active || !active.closest?.(".inspector-tabs")) return;
    nextTick(() => {
      if (!active.isConnected) overlayCanvasRef.value?.focus?.();
    });
  },
);

const chromeHidden = ref(false);
const chromeRevealTimestamp = ref(0);

// ── Full screen (F) ────────────────────────────────────────────────────────
//
// The real one (the Fullscreen API, which Electron turns into the window's own
// full screen): the title bar and the sidebar go (App.css, keyed on
// FULLSCREEN_CLASS), and so do the toolbar, the filmstrip and the inspector.
// Pointing at the top 48px brings the toolbar down, ending in a labelled way
// out; pointing at the right 48px brings out the edge tab, and I or the edge
// tab brings the inspector back. Esc leaves full screen
// before it leaves the lightbox. Space still hides the chrome in a window.
const FULLSCREEN_CLASS = "lightbox-fullscreen";
const FULLSCREEN_EDGE_PX = 48;
const FULLSCREEN_BAR_LINGER_MS = 1000;
const FULLSCREEN_TAP_BAR_MS = 3000;
const FULLSCREEN_NOTICE_MS = 3000;
const FULLSCREEN_IDLE_MS = 2000;
const fullscreenBarShown = ref(false);
const fullscreenNotice = ref(false);
// The pointer has rested: the stage's arrows and the cursor fade.
const fullscreenIdle = ref(false);
// Bumped as the pointer arrives at the right edge: the edge tab shows on it.
const edgeTabNudge = ref(0);
let pointerAtRightEdge = false;
// The notice is said once a visit.
let fullscreenNoticeSaid = false;
let fullscreenBarTimer = null;
let fullscreenNoticeTimer = null;
let fullscreenIdleTimer = null;

/** The document's own full screen, never a video's (its controls have one). */
function documentIsFullscreen() {
  return document.fullscreenElement === document.documentElement;
}

async function toggleFullscreen() {
  try {
    if (documentIsFullscreen()) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch (error) {
    console.warn(
      `Lightbox full screen could not be ${documentIsFullscreen() ? "left" : "entered"}:`,
      error,
    );
  }
}

function leaveFullscreen() {
  if (!documentIsFullscreen()) return;
  document.exitFullscreen().catch((error) => {
    console.warn("Lightbox full screen could not be left on close:", error);
  });
}

function onFullscreenChange() {
  const on = documentIsFullscreen() && open.value;
  fullscreen.value = on;
  document.documentElement.classList.toggle(FULLSCREEN_CLASS, on);
  fullscreenInspector.value = false;
  pointerAtRightEdge = false;
  hideFullscreenBar();
  clearTimeout(fullscreenNoticeTimer);
  fullscreenNotice.value = false;
  if (on && !fullscreenNoticeSaid) {
    fullscreenNoticeSaid = true;
    fullscreenNotice.value = true;
    fullscreenNoticeTimer = setTimeout(() => {
      fullscreenNotice.value = false;
    }, FULLSCREEN_NOTICE_MS);
  }
  noteFullscreenActivity();
}

/** A menu opened from the bar (Set, Project) keeps it down. */
function barMenuOpen() {
  return !!document.querySelector(
    ".v-overlay--active.v-menu, .overlay-topbar .ate.open",
  );
}

function scheduleFullscreenBarHide(ms) {
  clearTimeout(fullscreenBarTimer);
  fullscreenBarTimer = setTimeout(() => {
    fullscreenBarTimer = null;
    if (barMenuOpen()) scheduleFullscreenBarHide(ms);
    else fullscreenBarShown.value = false;
  }, ms);
}

function hideFullscreenBar() {
  clearTimeout(fullscreenBarTimer);
  fullscreenBarTimer = null;
  fullscreenBarShown.value = false;
}

/** The bar comes down while the pointer is at the top edge or on the bar, and
 * leaves a second after it goes. */
function noteFullscreenPointer(event) {
  if (!fullscreen.value) return;
  noteFullscreenActivity();
  const atTop =
    event.clientY <= FULLSCREEN_EDGE_PX ||
    !!event.target?.closest?.(".overlay-topbar");
  if (atTop) {
    clearTimeout(fullscreenBarTimer);
    fullscreenBarTimer = null;
    fullscreenBarShown.value = true;
  } else if (fullscreenBarShown.value && !fullscreenBarTimer) {
    scheduleFullscreenBarHide(FULLSCREEN_BAR_LINGER_MS);
  }
  // Tracked only while the tab is up for it, so closing the inspector with the
  // pointer in the zone still counts as an arrival on the next move.
  const atRight =
    edgeTabShown.value &&
    event.clientX >= window.innerWidth - FULLSCREEN_EDGE_PX;
  if (atRight && !pointerAtRightEdge) edgeTabNudge.value += 1;
  pointerAtRightEdge = atRight;
}

function noteFullscreenActivity() {
  clearTimeout(fullscreenIdleTimer);
  fullscreenIdle.value = false;
  if (!fullscreen.value) return;
  fullscreenIdleTimer = setTimeout(() => {
    fullscreenIdle.value = true;
  }, FULLSCREEN_IDLE_MS);
}

watch(open, (isOpen) => {
  if (isOpen) {
    fullscreenNoticeSaid = false;
    return;
  }
  leaveFullscreen();
  namingFaces.value = false;
});
// The zoom family's shared core (Compare's model, adopted here): continuous
// cursor-anchored wheel zoom, basis 1 = actual pixels, entry at fit, snap
// stops at fit and 100%. The floor policy is `rest`: the overlay is a
// DESTINATION, not a layer - wheeling out clamps hard at fit with no exit and
// no hysteresis (Escape/backdrop remain the exits; ZOOM_EXIT_RESISTANCE stays
// Compare-only). Pan transport stays the translate+scale transform on
// `.overlay-media`, which the face-bbox overlays, draw-mode rectangle, and
// video ride on.
const zoom = useWheelZoom({ floorPolicy: "rest" });
const zoomAnnouncement = zoom.announcement;
const isPanning = ref(false);
const lastPointer = ref({ x: 0, y: 0 });
const overlayExpandedStackIds = ref(new Set());
const overlayExpandedStackMembers = ref(new Map());
const overlayExpandedStackLoading = ref(new Set());
const overlayStackSignatures = ref(new Map());
const overlayStackReloadToken = ref(0);

// Frozen navigation backbone. The filmstrip and prev/next read membership from
// `allImages` (the grid's allGridImages). While the overlay is open we snapshot
// that sequence so the user's own edits (e.g. removing the tag a filtered view
// is filtered on) and any background refetch can't drop the current picture or
// its neighbours out from under the navigation. The snapshot is captured on
// open and cleared on close; on close ImageGrid applies the deferred grid
// reconcile so the now-non-matching picture leaves the grid. The currently
// displayed card stays fresh independently: it lives in `image.value` and is
// updated by local edits / fetchOverlayMetadata, not by this snapshot.
const frozenAllImages = ref(null);

// Membership source for everything that builds the navigation sequence. While
// open, read the frozen snapshot; otherwise read live `allImages`.
const overlayImages = computed(() => {
  if (
    open.value &&
    Array.isArray(frozenAllImages.value) &&
    frozenAllImages.value.length
  ) {
    return frozenAllImages.value;
  }
  return Array.isArray(allImages.value) ? allImages.value : [];
});

function captureFrozenAllImages() {
  const list = Array.isArray(allImages.value) ? allImages.value : [];
  frozenAllImages.value = list.slice();
}

const allImageById = computed(() => {
  const map = new Map();
  const list = overlayImages.value;
  for (const item of list) {
    if (!item || item.id == null) continue;
    map.set(String(item.id), item);
  }
  return map;
});

const allImagesByStackId = computed(() => {
  const map = new Map();
  const list = overlayImages.value;
  for (const item of list) {
    const stackId = getPictureStackId(item);
    if (!stackId || item?.id == null) continue;
    if (!map.has(stackId)) {
      map.set(stackId, []);
    }
    map.get(stackId).push(item);
  }
  for (const [stackId, members] of map.entries()) {
    map.set(stackId, sortStackMembers(members));
  }
  return map;
});

const allImageLeaderByStackId = computed(() => {
  const leaders = new Map();
  for (const [stackId, members] of allImagesByStackId.value.entries()) {
    const leader = members[0];
    if (leader?.id != null) {
      leaders.set(stackId, String(leader.id));
    }
  }
  return leaders;
});

function resetOverlayStackState() {
  overlayExpandedStackIds.value = new Set();
  overlayExpandedStackMembers.value = new Map();
  overlayExpandedStackLoading.value = new Set();
  overlayStackSignatures.value = new Map();
  overlayStackReloadToken.value += 1;
}

function applyInitialExpandedStackState() {
  const raw = Array.isArray(initialExpandedStackIds.value)
    ? initialExpandedStackIds.value
    : [];
  const next = new Set(
    raw
      .map((id) => (id === null || id === undefined ? "" : String(id)))
      .filter(Boolean),
  );
  overlayExpandedStackIds.value = next;
}

function setOverlayImageById(nextId) {
  if (nextId == null || nextId === "") {
    image.value = null;
    return;
  }
  const nextIdKey = String(nextId);
  const currentId = image.value?.id;
  const isSameImage =
    currentId !== null &&
    currentId !== undefined &&
    String(currentId) === nextIdKey;
  const targetFromAll = allImageById.value.get(nextIdKey);
  let target = targetFromAll
    ? targetFromAll
    : filmstripImageById.value.get(nextIdKey);
  if (!target && open.value) {
    // The picture did not exist when the overlay opened, so it is in the live
    // grid list but not in the snapshot frozen on open - an in-app ComfyUI
    // i2i/upscale stacking its output next to the picture being viewed is the
    // case that reaches here (ImageGrid's `update:overlayImageId` handler). The
    // freeze exists to stop a background refetch dropping the current picture
    // out of the navigation sequence, not to refuse an explicit move to a new
    // one, so re-capture it: the filmstrip and next/prev must agree with what
    // is on screen. Without this the move is a silent no-op and the overlay
    // stays on the old picture.
    const live = (Array.isArray(allImages.value) ? allImages.value : []).find(
      (item) => item?.id != null && String(item.id) === nextIdKey,
    );
    if (live) {
      captureFrozenAllImages();
      target = live;
    }
  }
  if (target) {
    const existingTags = getTagList(image.value?.tags);
    const targetTags = getTagList(target.tags);
    const existingDescription = image.value?.description;
    const existingSmartScore = image.value?.smartScore;
    const existingScore = image.value?.score;
    const existingFavourite = image.value?.favourite;
    // Captured beside the three above rather than read inline below, which is
    // correct only while the whole literal is evaluated before the assignment:
    // split it - build `next` then patch it, hoist a branch - and an inline
    // `image.value?.orientation` silently reads the value it is overwriting and
    // the preservation becomes a no-op, which is #1419 again.
    const existingOrientation = image.value?.orientation;
    const existingPixelSha = image.value?.pixel_sha;
    image.value = {
      ...target,
      // Preserve the existing description when re-setting the same image from filmstrip
      // data, which may only carry partial fields (no description). The full description
      // is loaded separately by fetchOverlayMetadata and must not be overwritten here.
      ...(isSameImage && existingDescription != null
        ? { description: existingDescription }
        : {}),
      // Preserve smartScore fetched by fetchOverlayMetadata - grid images don't carry it.
      ...(isSameImage && existingSmartScore != null
        ? { smartScore: existingSmartScore }
        : {}),
      // Preserve the locally-edited score. `target` here is the frozen navigation
      // snapshot captured on open, so it carries the pre-edit score. Re-applying it
      // for the same image would clobber an optimistic rating change (a 0 toggle is a
      // valid edit, hence the != null guard rather than a truthiness check).
      ...(isSameImage && existingScore != null ? { score: existingScore } : {}),
      // The heart likewise: toggling it replaces the grid's array, which lands
      // here with the snapshot's old value.
      ...(isSameImage && existingFavourite != null
        ? { favourite: existingFavourite }
        : {}),
      // Preserve the file's bytes for the same reason. A rotate's metadata read
      // moved them, and `target` may be a row that read never reached (an
      // expanded stack member from the filmstrip); re-applying its old
      // orientation puts the pre-rotate `?v=` back on the <img>.
      //
      // `target` is usually the sequence FROZEN when the overlay opened, and
      // this runs on every replacement of the `allImages` array - which is what
      // `ImageGrid.applyRotatedCards` does right after a rotate. So the same
      // clobber undid a turn that arrived over the socket: an undo turned the
      // picture back and the grid's own repaint turned it forward again a
      // moment later (#1419). Reading the snapshot rather than the array it is
      // handed, it did that even for a correctly-rotated new record.
      ...(isSameImage && existingOrientation != null
        ? { orientation: existingOrientation }
        : {}),
      ...(isSameImage && existingPixelSha != null
        ? { pixel_sha: existingPixelSha }
        : {}),
      tags: dedupeTagList(
        isSameImage ? (existingTags.length ? existingTags : targetTags) : [],
      ),
    };
  } else {
    if (!image.value) {
      image.value = { id: nextId, tags: [] };
    }
    return;
  }
  // Tag state reset is handled by OverlayTagsPanel's image-id watcher.
}

/**
 * The Edit tab's *Show it*: move to the picture its run made. The grid defers
 * `picture_imported` inserts while the lightbox is open (§9.1), so the result
 * is usually in neither the frozen snapshot nor the live list, and a plain
 * `initialImageId` move found nothing and did nothing. Its grid row is read by
 * id and slotted in after the picture the run started from, so next/prev and
 * the filmstrip agree with what is on screen; then the grid is told, so the
 * URL follows. A run's own result comes through here too (`showRunResult`),
 * which first checks the reader is still on the picture it was made from.
 */
async function showEditResult(id, sourceId) {
  const idKey = String(id);
  const fromId = image.value?.id;
  if (!open.value || idKey === "") return;
  // Stepping from the original: it is the before side of the compare.
  const beforeSrc =
    String(fromId) === String(sourceId) ? fullImageSrc.value : "";
  if (!allImageById.value.has(idKey) && !filmstripImageById.value.has(idKey)) {
    let row = null;
    try {
      const rows = await listPicturesByIds([idKey], { fields: "grid" });
      row = Array.isArray(rows) ? rows[0] : null;
    } catch (err) {
      console.warn(`Could not read edited picture ${idKey} to show it:`, err);
    }
    // Overtaken: the lightbox closed or the reader moved on meanwhile.
    if (!open.value || image.value?.id !== fromId) return;
    if (!row) {
      noticeStore.error("Couldn't open the edited picture. Try again.", {
        key: "edit-show-result",
      });
      return;
    }
    const list = overlayImages.value.slice();
    if (!list.some((item) => String(item?.id) === idKey)) {
      const anchor = String(sourceId ?? fromId);
      const at = list.findIndex((item) => String(item?.id) === anchor);
      list.splice(at === -1 ? list.length : at + 1, 0, row);
      frozenAllImages.value = list;
      // The stack grew by this read, not by a restructure seen on the grid:
      // re-record its signature, or the next grid repaint reads the change as
      // one and sends the reader back to the leader.
      const stackId = getPictureStackId(row);
      if (stackId && overlayStackSignatures.value.has(stackId)) {
        const next = new Map(overlayStackSignatures.value);
        next.set(stackId, getOverlayStackSignature(stackId));
        overlayStackSignatures.value = next;
      }
    }
  }
  setOverlayImageById(idKey);
  void ensureOverlayFilmstripForImage();
  if (beforeSrc) {
    compare.value = { resultId: idKey, beforeSrc, beforeId: String(sourceId) };
    comparePosition.value = 50;
  }
  emit("show-picture", id);
}

/**
 * A run started from the grid has landed a newer member in the stack of the
 * picture it was made from (`useGridComfyuiRuns`): go to it as *Show it*
 * does, unless the reader has since moved off that picture and its stack.
 * Decided here because only the lightbox knows what is on screen: the grid's
 * `overlayImageId` does not follow filmstrip steps.
 */
function showRunResult(id, sourceId, stackId) {
  const shown = image.value;
  if (!open.value || shown?.id == null) return;
  const onSource = String(shown.id) === String(sourceId);
  const inItsStack =
    stackId != null && String(getPictureStackId(shown) ?? "") === String(stackId);
  if (onSource || inItsStack) void showEditResult(id, sourceId);
}

const emit = defineEmits([
  "close",
  "apply-score",
  "apply-favourite",
  "set-guest-score",
  "add-tag",
  "update-description",
  "overlay-change",
  "added-to-set",
  "set-project",
  "run-plugin",
  "request-context-menu",
  "character-created",
  // The Recipe tab's "Run…" (#1407). The Run popup is mounted in App.vue, so
  // the lightbox asks for it rather than hosting a second one.
  "run-recipe",
  "use-as-input",
  // The Edit tab (#1381): step to its result, or open the Run popup on it.
  "show-picture",
  "edit-more-options",
]);

const descriptionPanelRef = ref(null);
const isDescriptionEditing = computed(
  () => descriptionPanelRef.value?.isEditingDescription ?? false,
);
const addToSetControlKey = ref(0);
const pluginMenuOpen = ref(false);
const starMenuOpen = ref(false);
let menuWasOpenOnPointerDown = false;
// A double-click in the grid opens the lightbox on its first click, so its
// second press lands here with a click count of 2: it would zoom (dblclick),
// hide the chrome or close on the backdrop. The first press after opening that
// already counts more than one click began outside, and is ignored. Every
// pointerdown clears the mark first: a cancelled pointerdown (pan, draw) sends
// no mousedown, so the mousedown alone would leave the mark stale.
let pressSeenSinceOpen = false;
let pressCarriedIn = false;
const overlaySelectedPluginName = ref("");
const overlayPluginParameters = ref({});

// Lock state for the currently displayed picture: drives the toolbar chip, the
// score/tags/description gating, and the panel lock notes.
const lockedSetsStore = useLockedSetsStore();
const userPrefsStore = useUserPrefsStore();
const isCurrentLocked = computed(() =>
  lockedSetsStore.isLocked(image.value?.id),
);
const currentLockReason = computed(() =>
  lockedSetsStore.lockReason(image.value?.id),
);

// ── Tag copy and paste (#2139) ─────────────────────────────────────────────
// Ctrl+V writes only where the picture is editable: the same test the
// sidebar's own editing uses.
const tagsEditable = computed(
  () => !isReadOnly.value && !isCurrentLocked.value,
);

/**
 * Ctrl+V anywhere in the lightbox: paste the copied tags onto the open
 * picture. The Tags panel stays mounted while the sidebar is closed or on
 * another tab, so this works whatever has focus.
 */
function pasteClipboardToImage() {
  if (!tagsEditable.value) return;
  void tagsPanelRef.value?.pasteClipboard();
}

// ── Rotate in place ────────────────────────────────────────────────────────
// One click (or one `[` / `]`) is one 90° step, applied immediately.
//
// Presses are SERIALISED, never dropped. Two quick presses are a legitimate
// 180° and the second one has to land, so a busy flag is the wrong guard here -
// it would throw away exactly the gesture the design promises. Letting them run
// concurrently is not an option either: each request reads the file's current
// orientation and writes the next one, so two in flight over one picture race,
// and one of the turns is silently lost. A chain gives both properties.
let rotateQueue = Promise.resolve();
// How many byte-change metadata reads are in flight per picture id. A COUNT,
// not a set of ids: the overlay's own rotate and the echo of that same rotate
// arriving over the socket can both be reading one picture at once, and with a
// bare set whichever finished first cleared the marker while the other was
// still out - dropping the cold-open unpin guard the later read was there to
// satisfy.
const rotateMetadataInFlightByImageId = new Map();

function isRotateMetadataInFlight(imageIdKey) {
  return (rotateMetadataInFlightByImageId.get(imageIdKey) || 0) > 0;
}

/** Why the rotate controls are greyed, or `null` when they are live. */
const rotateDisabledReason = computed(() =>
  image.value ? rotateBlockReason([image.value]) : null,
);
const canRotateCurrent = computed(
  () =>
    !isReadOnly.value &&
    !isCurrentLocked.value &&
    canRotateInPlace(image.value),
);

/**
 * The tooltip and the accessible name, which have to carry the refusal.
 *
 * A locked picture already has its own sentence (the toolbar chip states it),
 * so the lock wins over the format reason: naming the format when the real
 * blocker is a locked set would send the user off to convert a file that would
 * still be refused.
 */
function rotateTitle(side, shortcut) {
  if (isCurrentLocked.value) return currentLockReason.value;
  if (rotateDisabledReason.value) return rotateDisabledReason.value;
  return `Rotate ${side} (${shortcut})`;
}
const rotateLeftTitle = computed(() => rotateTitle("left", "["));
const rotateRightTitle = computed(() => rotateTitle("right", "]"));

/**
 * Re-read everything a rewrite of one picture's bytes invalidates.
 *
 * Shared by the rotate made HERE and by the `orientationUpdate` signal that carries
 * one made anywhere else (an undo or redo, another tab), so the two can never
 * disagree about what a turn costs:
 *
 *   * **metadata**, always. `orientation` moves and `mediaVersion` rebuilds the
 *     `<img>`'s cache-buster; a rotate leaves the pixels exactly where they
 *     were, so the orientation is the ONLY thing that can tell the browser the
 *     file it decoded is now sideways. Counted in
 *     `rotateMetadataInFlightByImageId` for the duration, so a cold-opened card
 *     pinned at `orientation: null` unpins on the value this read brings back
 *     rather than keeping the URL it was pinned to;
 *   * the **face and detection boxes** and the **text**. The boxes are drawn in
 *     the file's own coordinate space, which the turn just redefined, and the
 *     server drops the text on a turn. Re-read rather than transformed here:
 *     whatever the server now reports is what every other surface draws.
 *
 * Both callers KNOW a turn happened rather than inferring one - `runRotate`
 * from the ids the server said it turned, the watcher from the event's
 * `orientation` field. An earlier version compared the orientation before and
 * after the metadata read instead, which was wrong three ways: the shared
 * `metadataRequestId` can discard that read (leaving the lightbox stale with
 * no retry), navigating away and back rebuilds `image.value` under it, and a
 * NULL orientation - every video, and any row the backfill has not reached -
 * read as "turned" and re-read everything for changes that turned nothing.
 *
 * @param {number|string} imageId - the picture that was turned.
 * @returns {Promise<void>} settles once the metadata read has landed.
 */
async function refreshAfterTurn(imageId) {
  const imageIdKey = String(imageId);
  rotateMetadataInFlightByImageId.set(
    imageIdKey,
    (rotateMetadataInFlightByImageId.get(imageIdKey) || 0) + 1,
  );
  try {
    await fetchOverlayMetadata(imageId);
  } finally {
    const left = (rotateMetadataInFlightByImageId.get(imageIdKey) || 1) - 1;
    if (left > 0) rotateMetadataInFlightByImageId.set(imageIdKey, left);
    else rotateMetadataInFlightByImageId.delete(imageIdKey);
  }
  if (String(image.value?.id) !== imageIdKey) return;
  fetchFaceBboxes(imageId);
  fetchDetections(imageId);
  pictureText.refresh();
}

/**
 * One 90° step on one picture, plus the three refreshes it owes.
 *
 * They travel three different paths, which is why this is not a one-liner:
 *
 *   * the **operation log**, so the receipt narrates the step and offers undo.
 *     Anything the server refused rides that same pill as a second sentence -
 *     a separate notice would be the half the user dismisses;
 *   * the **overlay's own record** - metadata, boxes and text, which
 *     {@link refreshAfterTurn} owns because a turn made elsewhere costs
 *     exactly the same reads;
 *   * the **grid card behind us**, whose thumbnail URL carries a version only the
 *     server can recompute. `overlay-change` is the existing channel, and
 *     `fields.pixels` is what tells the grid this was a bitmap change rather
 *     than a metadata one.
 *
 * @param {number|string} imageId - captured when the gesture was made, not read
 *   at run time: the user may have navigated on while this waited its turn.
 * @param {string} direction - {@link ROTATE_CW} or {@link ROTATE_CCW}.
 * @returns {Promise<void>}
 */
async function runRotate(imageId, direction) {
  try {
    const result = await rotatePictures([imageId], direction);
    operationStore.noteNextReceipt(ROTATE_OP_TYPE, rotateSkipNote(result));
    operationStore.refresh();
    const rotated = Array.isArray(result?.rotated_picture_ids)
      ? result.rotated_picture_ids.map((id) => String(id))
      : [];
    // Nothing turned: the server refused this picture after all (a lock taken
    // between render and click, or a container the client gate misread). The
    // receipt already says so, so there is nothing left to refresh.
    if (!rotated.includes(String(imageId))) return;
    await refreshAfterTurn(imageId);
    emit("overlay-change", { imageId, fields: { pixels: true } });
  } catch (e) {
    console.error(`Rotate ${direction} failed for picture ${imageId}`, e);
    noticeStore.error(`Couldn't rotate that picture. ${errorDetail(e)}`, {
      key: "rotate-picture",
    });
  }
}

/**
 * Take one rotate gesture, behind whatever is already in flight.
 *
 * @param {string} direction - {@link ROTATE_CW} or {@link ROTATE_CCW}.
 * @returns {Promise<void>} settles when THIS step has landed.
 */
function rotateCurrentImage(direction) {
  const imageId = image.value?.id;
  if (!imageId || !canRotateCurrent.value) return rotateQueue;
  rotateQueue = rotateQueue.then(() => runRotate(imageId, direction));
  return rotateQueue;
}

// Remembered "stack outputs with originals" prefs (persisted in localStorage).
const genStackPrefs = useGenStackPrefsStore();
const stackFilterOutputs = computed({
  get: () => genStackPrefs.stackFilterOutputs,
  set: (val) => genStackPrefs.setStackFilterOutputs(val),
});
const overlaySelectionMedia = computed(() => {
  const format = image.value ? getOverlayFormat(image.value) : "";
  const hasVideos = format ? isSupportedVideoFile(format) : false;
  return {
    hasImages: !hasVideos,
    hasVideos,
  };
});

const overlayPluginOptions = computed(() => {
  if (!Array.isArray(availablePlugins.value)) return [];
  const hasImages = overlaySelectionMedia.value.hasImages;
  const hasVideos = overlaySelectionMedia.value.hasVideos;
  return availablePlugins.value.filter((plugin) => {
    if (!plugin || !plugin.name) return false;
    const supportsImages = plugin.supports_images !== false;
    const supportsVideos = plugin.supports_videos === true;
    if (hasImages && !supportsImages) return false;
    if (hasVideos && !supportsVideos) return false;
    return true;
  });
});

const activeOverlayPluginSchema = computed(() => {
  if (!overlaySelectedPluginName.value) return null;
  return (
    overlayPluginOptions.value.find(
      (plugin) =>
        String(plugin.name) === String(overlaySelectedPluginName.value),
    ) || null
  );
});


watch(open, (value) => {
  if (!value) {
    resetOverlayStackState();
    // Release the frozen navigation backbone so the next open re-snapshots the
    // current grid, and so closed-overlay reads fall through to live allImages.
    frozenAllImages.value = null;
    pluginMenuOpen.value = false;
    chromeHidden.value = false;
    chromeRevealTimestamp.value = 0;
    addToSetControlKey.value += 1;
    zoom.reset();
  } else {
    // Snapshot the grid sequence up front so prev/next stay stable for the
    // whole lifetime of the overlay regardless of edits or background refetches.
    captureFrozenAllImages();
    resetOverlayStackState();
    applyInitialExpandedStackState();
    pluginMenuOpen.value = false;
    chromeRevealTimestamp.value = Date.now();
    fetchCharacters();
  }
});

watch(
  overlayPluginOptions,
  (plugins) => {
    if (!Array.isArray(plugins) || !plugins.length) {
      overlaySelectedPluginName.value = "";
      return;
    }
    if (!overlaySelectedPluginName.value) {
      overlaySelectedPluginName.value = String(plugins[0].name);
      return;
    }
    const exists = plugins.some(
      (plugin) =>
        String(plugin.name) === String(overlaySelectedPluginName.value),
    );
    if (!exists) {
      overlaySelectedPluginName.value = String(plugins[0].name);
    }
  },
  { immediate: true },
);

watch(overlaySelectedPluginName, () => {
  overlayPluginParameters.value = {};
});

watch(pluginMenuOpen, (isOpen) => {
  if (!isOpen) return;
  if (!overlaySelectedPluginName.value && overlayPluginOptions.value.length) {
    overlaySelectedPluginName.value = String(
      overlayPluginOptions.value[0].name,
    );
  }
  overlayPluginParameters.value = {};
});

function runOverlayPlugin() {
  if (!image.value?.id || !overlaySelectedPluginName.value) return;
  emit("run-plugin", {
    pluginName: overlaySelectedPluginName.value,
    pictureIds: [image.value.id],
    parameters: overlayPluginParameters.value || {},
    stack: stackFilterOutputs.value,
  });
  pluginMenuOpen.value = false;
}

function getFullImageUrl(targetImage = null) {
  const data = targetImage || image.value;
  return appendShareToken(
    buildMediaUrl({ backendUrl: backendUrl.value, image: data }),
  );
}

function getOverlayImageList() {
  const expanded = filmstripImages.value;
  if (Array.isArray(expanded) && expanded.length) return expanded;
  return overlayImages.value;
}

const filmstripImageById = computed(() => {
  const map = new Map();
  const list = getOverlayImageList();
  for (const item of list) {
    if (!item || item.id == null) continue;
    map.set(String(item.id), item);
  }
  return map;
});

const filmstripIndexById = computed(() => {
  const map = new Map();
  const list = filmstripImages.value;
  for (let idx = 0; idx < list.length; idx += 1) {
    const item = list[idx];
    if (!item || item.id == null) continue;
    map.set(String(item.id), idx);
  }
  return map;
});

function isImageInFilmstrip(targetId) {
  if (!targetId) return false;
  const list = filmstripImages.value;
  if (!Array.isArray(list) || !list.length) return false;
  return list.some((item) => String(item?.id) === String(targetId));
}

async function ensureOverlayFilmstripForImage() {
  const targetId = image.value?.id ?? null;
  if (!targetId) return;
  const stackId = getPictureStackId(image.value);
  if (!stackId) return;
  const shouldExpand = !isImageInFilmstrip(targetId) && !!stackId;
  if (!shouldExpand) return;
  const stackCount = getOverlayStackCount(image.value);
  if (stackCount <= 1) return;
  if (!overlayExpandedStackIds.value.has(stackId)) {
    const nextIds = new Set(overlayExpandedStackIds.value);
    nextIds.add(stackId);
    overlayExpandedStackIds.value = nextIds;
  }
  await ensureOverlayStackMembersLoaded(stackId, image.value);
}

function getOverlayLocalStackMembers(stackId) {
  if (!stackId) return [];
  const members = allImagesByStackId.value.get(stackId);
  return Array.isArray(members) ? members : [];
}

function getOverlayStackSignature(stackId) {
  if (!stackId) return "";
  const members = getOverlayLocalStackMembers(stackId);
  if (!members.length) return "";
  const parts = members.map((img) => {
    const id = img?.id != null ? String(img.id) : "";
    const pos = getStackPositionValue(img);
    return `${id}:${pos === null ? "x" : String(pos)}`;
  });
  return `${members.length}|${parts.join(",")}`;
}

function normalizeOverlayStackMembersForStack(stackId, members) {
  if (!stackId || !Array.isArray(members) || !members.length) return [];
  const normalized = [];
  for (const member of members) {
    if (!member || member.id == null) continue;
    const id = String(member.id);
    const latest = allImageById.value.get(id) || member;
    if (getPictureStackId(latest) !== stackId) continue;
    normalized.push(latest);
  }
  return sortStackMembers(normalized);
}

const overlayStackCounts = computed(() => {
  const counts = new Map();
  const list = overlayImages.value;
  for (const img of list) {
    const stackId = getPictureStackId(img);
    if (!stackId) continue;
    counts.set(stackId, (counts.get(stackId) || 0) + 1);
  }
  return counts;
});

function getOverlayStackCount(item) {
  const count = Number(item?.stackCount ?? item?.stack_count ?? 0);
  if (Number.isFinite(count) && count > 0) return count;
  const stackId = getPictureStackId(item);
  if (!stackId) return 0;
  const expanded = overlayExpandedStackMembers.value.get(stackId);
  const ids = Array.isArray(expanded?.ids) ? expanded.ids : [];
  if (ids.length) return ids.length;
  return overlayStackCounts.value.get(stackId) || 0;
}

function getOverlayStackColor(item) {
  if (!item) return null;
  if (typeof item.stackColor === "string" && item.stackColor) {
    return item.stackColor;
  }
  const stackIndex =
    typeof item.stackIndex === "number"
      ? item.stackIndex
      : typeof item.stack_index === "number"
        ? item.stack_index
        : null;
  if (typeof stackIndex === "number") {
    return getStackColor(stackIndex);
  }
  const stackId = getPictureStackId(item);
  const index = getStackColorIndexFromId(stackId);
  if (index === null) return null;
  return getStackColor(index);
}

function buildOverlayExpandedStackImages(stackId, fallbackItem, stackCount) {
  const entry = overlayExpandedStackMembers.value.get(stackId);
  const images = Array.isArray(entry?.images) ? entry.images : [];
  const normalizedCached = normalizeOverlayStackMembersForStack(
    stackId,
    images,
  );
  const localMembers = getOverlayLocalStackMembers(stackId);
  const imageById = new Map();
  for (const img of normalizedCached) {
    if (!img || img.id == null) continue;
    imageById.set(String(img.id), img);
  }
  for (const img of localMembers) {
    if (!img || img.id == null) continue;
    imageById.set(String(img.id), img);
  }
  const ordered = [];
  const seen = new Set();
  const addImage = (img) => {
    if (!img || img.id == null) return;
    const key = String(img.id);
    if (seen.has(key)) return;
    seen.add(key);
    ordered.push(img);
  };

  const orderedIds = sortStackMembers(Array.from(imageById.values())).map(
    (img) => String(img.id),
  );
  for (const id of orderedIds) {
    addImage(imageById.get(String(id)));
  }

  if (fallbackItem?.id != null) {
    addImage(fallbackItem);
  }

  if (ordered.length) {
    ordered[0] = { ...ordered[0], stackCount };
  }
  return ordered;
}

function collapseOverlayStackImages(images) {
  if (!Array.isArray(images) || images.length === 0) return [];
  const counts = new Map();
  for (const img of images) {
    const stackId = getPictureStackId(img);
    if (!stackId) continue;
    counts.set(stackId, (counts.get(stackId) || 0) + 1);
  }
  if (!counts.size) return images;
  const leaders = allImageLeaderByStackId.value;
  const seen = new Set();
  const collapsed = [];
  for (const img of images) {
    const stackId = getPictureStackId(img);
    if (!stackId) {
      collapsed.push(img);
      continue;
    }
    const leaderId = leaders.get(stackId);
    if (leaderId && img?.id != null && String(img.id) !== leaderId) {
      continue;
    }
    if (seen.has(stackId)) continue;
    seen.add(stackId);
    const stackCount = getOverlayStackCount(img) || counts.get(stackId) || 1;
    if (overlayExpandedStackIds.value.has(stackId)) {
      const expanded = buildOverlayExpandedStackImages(
        stackId,
        img,
        stackCount,
      );
      if (expanded.length) {
        collapsed.push(...expanded);
        continue;
      }
    }
    collapsed.push({
      ...img,
      stackCount,
    });
  }
  return collapsed;
}

async function ensureOverlayStackMembersLoaded(
  stackId,
  referenceItem = null,
  options = {},
) {
  if (!stackId) return false;
  const forceReload = options?.force === true;
  const localMembers = getOverlayLocalStackMembers(stackId);
  const expectedCount = Number(
    referenceItem?.stackCount ?? referenceItem?.stack_count ?? 0,
  );
  if (!forceReload && localMembers.length > 1) {
    const orderedLocal = sortStackMembers(localMembers);
    if (!Number.isFinite(expectedCount) || expectedCount <= 0) {
      const ids = orderedLocal
        .filter((img) => img && img.id != null)
        .map((img) => String(img.id));
      const nextMembers = new Map(overlayExpandedStackMembers.value);
      nextMembers.set(stackId, { ids, images: orderedLocal });
      overlayExpandedStackMembers.value = nextMembers;
      return true;
    }
    if (orderedLocal.length >= expectedCount) {
      const ids = orderedLocal
        .filter((img) => img && img.id != null)
        .map((img) => String(img.id));
      const nextMembers = new Map(overlayExpandedStackMembers.value);
      nextMembers.set(stackId, { ids, images: orderedLocal });
      overlayExpandedStackMembers.value = nextMembers;
      return true;
    }
  }
  const existing = overlayExpandedStackMembers.value.get(stackId);
  if (
    !forceReload &&
    existing &&
    Array.isArray(existing.images) &&
    existing.images.length
  ) {
    if (
      !Number.isFinite(expectedCount) ||
      expectedCount <= 0 ||
      existing.images.length >= expectedCount
    ) {
      return true;
    }
  }
  if (overlayExpandedStackLoading.value.has(stackId)) return false;
  if (!backendUrl.value) return false;
  const nextLoading = new Set(overlayExpandedStackLoading.value);
  nextLoading.add(stackId);
  overlayExpandedStackLoading.value = nextLoading;
  try {
    const data = await listStackPictures(stackId, {
      baseUrl: backendUrl.value,
    });
    const images = Array.isArray(data) ? data : [];
    const ordered = normalizeOverlayStackMembersForStack(stackId, images);
    const ids = ordered
      .filter((img) => img && img.id != null)
      .map((img) => String(img.id));
    const nextMembers = new Map(overlayExpandedStackMembers.value);
    nextMembers.set(stackId, { ids, images: ordered });
    overlayExpandedStackMembers.value = nextMembers;
    return true;
  } catch (e) {
    console.error("Failed to load overlay stack members:", e);
    return false;
  } finally {
    const cleared = new Set(overlayExpandedStackLoading.value);
    cleared.delete(stackId);
    overlayExpandedStackLoading.value = cleared;
  }
}

function getOverlayStackLeaderId(stackId) {
  if (!stackId) return null;
  const localMembers = getOverlayLocalStackMembers(stackId);
  if (localMembers.length && localMembers[0]?.id != null) {
    return String(localMembers[0].id);
  }
  const cached = overlayExpandedStackMembers.value.get(stackId);
  const cachedIds = Array.isArray(cached?.ids) ? cached.ids : [];
  if (cachedIds.length) {
    return String(cachedIds[0]);
  }
  const cachedImages = Array.isArray(cached?.images) ? cached.images : [];
  const orderedCached = sortStackMembers(cachedImages);
  if (orderedCached.length && orderedCached[0]?.id != null) {
    return String(orderedCached[0].id);
  }
  return null;
}

async function toggleFilmstripStackExpand(item) {
  const stackId = getPictureStackId(item);
  if (!stackId) return;
  if (overlayExpandedStackIds.value.has(stackId)) {
    const currentStackId = getPictureStackId(image.value);
    if (currentStackId === stackId && image.value?.id != null) {
      // Always navigate to the leader when collapsing, regardless of showStacks.
      // If the current image is a non-leader it will no longer be visible in the
      // collapsed filmstrip; staying on it would cause ensureOverlayFilmstripForImage
      // to immediately re-expand the stack on the next allImages poll or metadata fetch.
      const leaderId = getOverlayStackLeaderId(stackId);
      if (leaderId && leaderId !== String(image.value.id)) {
        setOverlayImageById(leaderId);
      }
    }
    const nextIds = new Set(overlayExpandedStackIds.value);
    nextIds.delete(stackId);
    overlayExpandedStackIds.value = nextIds;
    return;
  }
  const nextIds = new Set(overlayExpandedStackIds.value);
  nextIds.add(stackId);
  overlayExpandedStackIds.value = nextIds;
  await ensureOverlayStackMembersLoaded(stackId, item);
}

function prefetchFilmstripStackMembers(item) {
  const stackId = getPictureStackId(item);
  if (!stackId) return;
  void ensureOverlayStackMembersLoaded(stackId, item);
}

// ── Make cover / Move earlier / Move later ──────────────────────────────────
// The lightbox keeps its own copy of each stack (the filmstrip is frozen while
// it is open), so it reorders that copy itself and tells the grid, which
// refetches on close like any other stack change made here.

/** A stack's members in order, cover first, as the filmstrip draws them. */
function getOverlayStackOrder(stackId) {
  if (!stackId) return [];
  return buildOverlayExpandedStackImages(stackId, null, 0).map((img) =>
    String(img.id),
  );
}

/**
 * Where a picture sits in its stack, or null while that is not known: a
 * position read off part of a stack would be a wrong number, not a rough one.
 */
function getOverlayStackPlace(item) {
  const stackId = getPictureStackId(item);
  if (!stackId || item?.id == null) return null;
  const count = getOverlayStackCount(item);
  if (count < 2) return null;
  const ids = getOverlayStackOrder(stackId);
  if (ids.length !== count) return null;
  const position = ids.indexOf(String(item.id));
  return position === -1 ? null : { position, count };
}

const overlayStackPlace = computed(() => getOverlayStackPlace(image.value));

// The header chip and the menu need the order of the picture on screen even
// when its stack is folded in the filmstrip.
watch(
  () => getPictureStackId(image.value),
  (stackId) => {
    if (stackId && getOverlayStackCount(image.value) > 1) {
      void ensureOverlayStackMembersLoaded(stackId, image.value);
    }
  },
  { immediate: true },
);

function applyOverlayStackOrder(stackId, orderedIds) {
  const positions = new Map(orderedIds.map((id, idx) => [String(id), idx]));
  const reposition = (img) =>
    img?.id != null &&
    getPictureStackId(img) === stackId &&
    positions.has(String(img.id))
      ? { ...img, stack_position: positions.get(String(img.id)) }
      : img;
  const entry = overlayExpandedStackMembers.value.get(stackId);
  if (entry) {
    const nextMembers = new Map(overlayExpandedStackMembers.value);
    nextMembers.set(stackId, {
      ids: orderedIds.slice(),
      images: (entry.images || []).map(reposition),
    });
    overlayExpandedStackMembers.value = nextMembers;
  }
  if (Array.isArray(frozenAllImages.value)) {
    frozenAllImages.value = frozenAllImages.value.map(reposition);
  }
  if (image.value) image.value = reposition(image.value);
}

const overlayOrderSaves = new Map();

/**
 * One step of reordering the stack a picture is in, saved straight away. The
 * lightbox stays on the same picture; only its place changes.
 *
 * @param {string|number} pictureId
 * @param {"cover"|"earlier"|"later"} where
 */
async function moveStackMember(pictureId, where) {
  if (isReadOnly.value || pictureId == null) return;
  const item =
    String(image.value?.id) === String(pictureId)
      ? image.value
      : filmstripImages.value.find((img) => String(img?.id) === String(pictureId));
  const stackId = getPictureStackId(item);
  if (!stackId) return;
  await ensureOverlayStackMembersLoaded(stackId, item);
  if (!getOverlayStackPlace(item)) return;
  const currentIds = getOverlayStackOrder(stackId);
  const nextIds = moveStackMemberInOrder(currentIds, pictureId, where);
  if (!nextIds) return;
  applyOverlayStackOrder(stackId, nextIds);
  // Only the latest save may roll back; see useStackOrdering's persistStackOrder.
  const token = (overlayOrderSaves.get(stackId) || 0) + 1;
  overlayOrderSaves.set(stackId, token);
  try {
    await setStackOrder(stackId, nextIds.map(Number));
    emit("overlay-change", {
      imageId: pictureId,
      fields: { stack: true },
      stackId,
    });
  } catch (err) {
    console.error(`Failed to save the order of stack ${stackId}`, err);
    if (overlayOrderSaves.get(stackId) === token) {
      applyOverlayStackOrder(stackId, currentIds);
    }
    noticeStore.error(
      `Couldn't save the stack order. ${errorDetail(err) || "The previous order was restored."}`,
      { key: "stack-order-save" },
    );
  }
}

const filmstripImages = computed(() => {
  return collapseOverlayStackImages(overlayImages.value);
});

watch(
  () => initialImageId.value,
  (newId) => {
    setOverlayImageById(newId);
    void ensureOverlayFilmstripForImage();
  },
  { immediate: true },
);

const pendingAllImagesUpdate = ref(false);

async function applyAllImagesUpdate() {
  const currentId = image.value?.id ?? initialImageId.value;
  if (currentId != null && currentId !== "") {
    setOverlayImageById(currentId);
  }
  void ensureOverlayFilmstripForImage();

  const stackId = getPictureStackId(image.value);
  if (!stackId) return;
  const nextSignature = getOverlayStackSignature(stackId);
  if (!nextSignature) return;

  const previousSignature = overlayStackSignatures.value.get(stackId) || "";
  const nextSignatures = new Map(overlayStackSignatures.value);
  nextSignatures.set(stackId, nextSignature);
  overlayStackSignatures.value = nextSignatures;

  if (previousSignature === nextSignature) return;

  if (previousSignature) {
    emit("overlay-change", {
      imageId: image.value?.id ?? null,
      fields: { stack: true },
      stackId,
    });
  }

  const nextMembers = new Map(overlayExpandedStackMembers.value);
  nextMembers.delete(stackId);
  overlayExpandedStackMembers.value = nextMembers;

  const reloadToken = overlayStackReloadToken.value + 1;
  overlayStackReloadToken.value = reloadToken;

  await ensureOverlayStackMembersLoaded(stackId, image.value, {
    force: true,
  });
  if (overlayStackReloadToken.value !== reloadToken) return;

  // Only navigate to the new stack leader when the stack structure genuinely
  // changed while we were already watching it (previousSignature is truthy).
  // When previousSignature is "" this is just the first time we record the
  // signature; navigating away from the user's deliberate non-leader selection
  // here would be wrong (and is the root cause of the tag-change nav bug).
  if (!previousSignature) return;

  const refreshed = overlayExpandedStackMembers.value.get(stackId);
  const ids = Array.isArray(refreshed?.ids) ? refreshed.ids : [];
  const localMembers = getOverlayLocalStackMembers(stackId);
  const topId =
    ids.length > 0
      ? ids[0]
      : localMembers[0]?.id != null
        ? String(localMembers[0].id)
        : null;
  if (topId && String(image.value?.id ?? "") !== String(topId)) {
    setOverlayImageById(topId);
  }
}

watch(
  () => allImages.value,
  async () => {
    // Don't disturb the DOM while the user is actively typing - the reactive
    // update to image.value causes a DOM patch that can blur the focused input.
    // Set a flag so we apply the update as soon as editing finishes.
    if (isAddingTag.value || isDescriptionEditing.value) {
      pendingAllImagesUpdate.value = true;
      return;
    }
    await applyAllImagesUpdate();
  },
);

// Flush any deferred allImages update as soon as the user finishes editing.
watch(
  () => isAddingTag.value || isDescriptionEditing.value,
  async (isEditing) => {
    if (!isEditing && pendingAllImagesUpdate.value) {
      pendingAllImagesUpdate.value = false;
      await applyAllImagesUpdate();
    }
  },
);

watch(showStacks, (value) => {
  if (value) {
    // Ensure the current image is visible; if it's a non-leader stack member
    // that isn't in the filmstrip, ensureOverlayFilmstripForImage will expand
    // just that stack as needed.
    void ensureOverlayFilmstripForImage();
  } else {
    overlayExpandedStackIds.value = new Set();
  }
});

watch(open, (isOpen) => {
  pressSeenSinceOpen = false;
  pressCarriedIn = false;
  if (!isOpen) {
    descriptionPanelRef.value?.cancelEditDescription();
    descriptionPanelRef.value?.resetCopyState();
    closeFallbackSaveDialog();
  }
});

function setScore(n) {
  if (!image.value) return;
  if (isReadOnly.value) {
    emit("set-guest-score", image.value, n);
    return;
  }
  // Score is label/curation data: a locked picture can't be re-scored.
  if (isCurrentLocked.value) return;
  image.value.score = toggleScore(image.value.score, n);
  emit("apply-score", image.value, image.value.score);
}

// The owner's heart is not label data, so a locked picture can still take one.
function toggleFavourite() {
  if (!image.value || isReadOnly.value || !props.favouritable) return;
  image.value.favourite = !image.value.favourite;
  emit("apply-favourite", image.value, image.value.favourite);
}

function showPrevImage() {
  return navigateOverlayImage(-1);
}

function navigateOverlayImage(direction) {
  const sorted = filmstripImages.value;
  if (!image.value || !sorted.length) return;
  const idx = filmstripIndexById.value.get(String(image.value.id)) ?? -1;
  if (idx === -1) return;
  const nextIdx = (idx + direction + sorted.length) % sorted.length;
  setOverlayImageById(sorted[nextIdx]?.id ?? null);
}

function selectImageByIndex(idx) {
  const list = filmstripImages.value;
  if (!Array.isArray(list)) return;
  const target = list[idx];
  if (target) {
    setOverlayImageById(target.id ?? null);
  }
}

function showNextImage() {
  return navigateOverlayImage(1);
}

/**
 * Is the keystroke landing in a text-entry surface?
 *
 * A text field keeps its own native undo stack, and the lightbox has several
 * (the tag field, the description editor, the plugin parameter inputs). Both
 * the event target and `document.activeElement` count, matching the strictness
 * App's global handler uses: a Vuetify combobox moves focus around, so the two
 * do not always agree.
 *
 * @param {EventTarget|null} target - the keydown target.
 * @returns {boolean}
 */
function hasNativeCopyContext(target) {
  if (isTypingTarget(target)) return true;
  const selection =
    typeof window === "undefined" ? null : window.getSelection?.();
  return Boolean(selection && !selection.isCollapsed && selection.toString());
}

function handleKeydown(e) {
  if (!open.value) return;
  // A menu over the lightbox takes its own Escape: the title bar's, which sits
  // beside the lightbox now (#2163), or one of the lightbox's own. Vuetify
  // listens on `window` only once a menu opens, after this listener, so the
  // stop below would otherwise close the picture under an open menu. Menus
  // and dialogs only: a snackbar is persistent and ignores Escape, and the
  // read-only guest banner is one that stays up.
  if (
    e.key === "Escape" &&
    document.querySelector(
      ".v-overlay--active.v-menu, .v-overlay--active.v-dialog",
    )
  ) {
    return;
  }
  // Prevent other window keydown listeners (e.g. ImageGrid) from seeing this
  // event while the overlay is open. ImageOverlay mounts before ImageGrid so
  // its handler runs first; without this, ImageGrid would process the same
  // keypress (e.g. Escape) after the overlay has already handled it.
  e.stopImmediatePropagation();
  // The Run popup the Edit tab opened sits over the lightbox: its keys are its
  // own, and an arrow or Escape must not step or close the lightbox below.
  if (runDialogStore.source || runDialogStore.makeMore) return;
  if (filmstripMenu.value) {
    if (e.key === "Escape") closeFilmstripMenu();
    return;
  }

  // Shift held draws every box; a capital typed into a field does not.
  if (e.key === "Shift") {
    if (!isTypingTarget(e.target)) shiftHeld.value = true;
    return;
  }

  // Full screen is left before anything else Escape does, as the browser's
  // own Escape would.
  if (e.key === "Escape" && fullscreen.value && !isEditableElement(e.target)) {
    e.preventDefault();
    void toggleFullscreen();
    return;
  }

  // Keyboard access to the media context menu (Shift+F10 / ContextMenu key),
  // available regardless of chrome visibility. Suppressed while typing so the
  // native menu (paste / spellcheck) still works in the tag and description
  // fields.
  if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
    if (!isEditableElement(e.target)) {
      e.preventDefault();
      handleUserActivity();
      openContextMenuFromKeyboard();
      return;
    }
  }

  // Undo / redo, handled HERE rather than by App's global binding.
  //
  // App's handler stands down while `.image-overlay` is in the DOM (an
  // explicit guard at its top). Do NOT rely on the stopImmediatePropagation()
  // above to silence it: that only works when this listener registered first,
  // and the Duplicates view's grid remount re-registers this one LAST - that
  // ordering flip is exactly how one Ctrl+Z once ran two undos. The owner
  // ruled that undo must work here, fitted to the lightbox's own GUI:
  // `OverlayActionReceipt` narrates the result inside the overlay chrome, so
  // the action is never taken blind.
  //
  // Above the `chromeHidden` bail on purpose. The narration is a transient HUD
  // like the progress cards and the swipe hint, none of which hide with the
  // chrome, so undo stays reachable on a bare image and still reports itself.
  if (
    (e.ctrlKey || e.metaKey) &&
    !e.altKey &&
    !e.repeat &&
    !isTypingTarget(e.target)
  ) {
    const commandKey = e.key?.toLowerCase();
    if (!e.shiftKey && commandKey === "s" && image.value?.id) {
      e.preventDefault();
      saveMedia(buildContextMenuImage());
      return;
    }
    // Chips selected in the sidebar: Ctrl+C copies those tags, not the media.
    if (
      !e.shiftKey &&
      commandKey === "c" &&
      tagsPanelRef.value?.hasSelection &&
      !hasNativeCopyContext(e.target)
    ) {
      e.preventDefault();
      void tagsPanelRef.value.copySelection();
      return;
    }
    if (
      !e.shiftKey &&
      commandKey === "c" &&
      !hasNativeCopyContext(e.target) &&
      copyAvailability().available
    ) {
      e.preventDefault();
      copyMedia(buildContextMenuImage());
      return;
    }
    if (isReadOnly.value) return;
    if (commandKey === "v" && !e.shiftKey) {
      // Ctrl+V pastes the copied tags onto the picture, wherever focus is;
      // a text field keeps its own paste (the guard on this block).
      if (tagsEditable.value) {
        e.preventDefault();
        pasteClipboardToImage();
      }
      return;
    }
    if (commandKey === "z" && !e.shiftKey) {
      e.preventDefault();
      // The selection's own steps first, while there are any to take back.
      if (paintActive.value && paint.hasSelection.value) paint.undo();
      else operationStore.undo();
      return;
    }
    if (commandKey === "y" || (commandKey === "z" && e.shiftKey)) {
      e.preventDefault();
      // As undo: while there is a selection, or a step of one to bring back,
      // redo is the selection's, never a library change out of sight.
      if (
        paintActive.value &&
        (paint.hasSelection.value || paint.canRedo.value)
      ) {
        paint.redo();
      } else {
        operationStore.redo();
      }
      return;
    }
  }

  // When chrome is hidden, only Space and Escape reveal it - other keys still
  // navigate/act but don't bring the chrome back.
  if (chromeHidden.value) {
    if (e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      handleUserActivity();
      return;
    }
    // Let navigation keys work silently without revealing chrome
    if (
      [
        "ArrowLeft",
        "Left",
        "ArrowRight",
        "Right",
        "ArrowUp",
        "Up",
        "ArrowDown",
        "Down",
      ].includes(e.key)
    ) {
      // The step is the key's whole job: left to its default, Chromium also
      // scrolls the last-clicked scroller (the filmstrip) 40px, after the
      // strip has centred the picture. A text field keeps its caret.
      if (!isEditableElement(e.target)) e.preventDefault();
      if (["ArrowLeft", "Left", "ArrowUp", "Up"].includes(e.key))
        showPrevImage();
      else showNextImage();
      return;
    }
    // Escape reveals chrome (and closes draw mode if active)
    if (e.key === "Escape") {
      handleUserActivity();
      return;
    }
    // T reveals chrome, opens the sidebar, and starts tag entry
    if (e.key === "t" || e.key === "T") {
      e.preventDefault();
      handleUserActivity();
      if (!isReadOnly.value) beginTagEntry();
      return;
    }
    // All other keys: ignore while chrome is hidden
    return;
  }

  handleUserActivity();

  if (isDescriptionEditing.value || isAddingTag.value) {
    if (e.key === "Escape") {
      if (isDescriptionEditing.value) {
        descriptionPanelRef.value?.cancelEditDescription();
      } else if (isAddingTag.value) {
        tagsPanelRef.value?.cancelAddTag();
      }
    }
    return;
  }

  // Block shortcuts when any other editable element (e.g. plugin parameter inputs) has focus.
  // Still allow ESC to close the plugin menu if open.
  const target = e.target;
  if (isEditableElement(target)) {
    if (e.key === "Escape") {
      if (pluginMenuOpen.value) {
        pluginMenuOpen.value = false;
        e.preventDefault();
      } else if (target.tagName === "SELECT") {
        // Close the select dropdown on ESC, since it doesn't do that by default.
        target.blur();
      } else if (target.type === "range" || target.type === "checkbox") {
        // Nothing is typed into a slider or a checkbox: a Refine slider
        // just let go of must not stop Escape clearing the selection.
        target.blur();
      } else {
        return;
      }
    } else {
      return;
    }
  }

  // Space is a focused button's own key. Without this the chrome toggle below
  // took it, so no button in the pane (a Tasks row's Stop, the Edit tab's Run)
  // could be pressed with it. Only a keyboard-focused one: a button just
  // clicked keeps focus without `:focus-visible`, and Space then still hides
  // the chrome as it always has.
  if (
    (e.key === " " || e.key === "Spacebar") &&
    e.target?.closest?.(".overlay-sidebar button:focus-visible")
  ) {
    return;
  }

  // Tag chips selected: Escape clears them, Delete removes them.
  if (tagsPanelRef.value?.hasSelection) {
    if (e.key === "Escape") {
      e.preventDefault();
      tagsPanelRef.value.clearSelection();
      return;
    }
    if (
      (e.key === "Delete" || e.key === "Backspace") &&
      !e.ctrlKey &&
      !e.metaKey &&
      !e.altKey
    ) {
      e.preventDefault();
      if (tagsEditable.value) void tagsPanelRef.value.removeSelection();
      return;
    }
  }

  if (
    paintActive.value &&
    // Under a comparison the selection is out of sight: keys must not change it.
    !compareShown.value &&
    !e.ctrlKey &&
    !e.metaKey &&
    !e.altKey &&
    handlePaintKey(e.key)
  ) {
    e.preventDefault();
    return;
  }

  if (e.key === "Escape") {
    // Focus is inside the receipt: retire it and hand the keyboard back to the
    // canvas, without closing the lightbox. A keyboard user who tabbed into the
    // pill needs an exit that is not "leave the whole surface". Escape is NOT a
    // general receipt dismissal - the pill blocks nothing and expires on its
    // own, and making Escape-to-close depend on an invisible countdown would be
    // a worse failure than a pill that outlived its welcome.
    if (receiptRef.value?.containsFocus?.()) {
      receiptRef.value.dismiss();
      overlayCanvasRef.value?.focus?.();
      return;
    }
    if (compareShown.value) {
      // The comparison, not the lightbox.
      closeCompare();
      return;
    }
    if (paintActive.value && (paint.draft.value || paint.cropBox.value)) {
      // The gesture or the crop frame, not the lightbox.
      paint.cancel();
      paint.cropBox.value = null;
    } else if (paintActive.value && paint.tool.value) {
      // The tool goes down before anything closes.
      paint.setTool(null);
    } else if (paintActive.value && paint.hasSelection.value) {
      // The selection goes first; the next Escape closes the lightbox.
      paint.clear();
    } else if (filmstripPicks.value.length) {
      filmstripPicks.value = [];
    } else if (drawMode.value) {
      clearDrawMode();
    } else if (pluginMenuOpen.value) {
      pluginMenuOpen.value = false;
    } else if (namingFaces.value) {
      // The Faces panel, not the lightbox, wherever focus was left.
      namingFaces.value = false;
      facesPencilRef.value?.focus?.();
    } else {
      emit("close");
    }
  } else if (["ArrowLeft", "Left", "ArrowUp", "Up"].includes(e.key)) {
    // As with the chrome hidden: no native scroll on top of the step.
    e.preventDefault();
    showPrevImage();
  } else if (["ArrowRight", "Right", "ArrowDown", "Down"].includes(e.key)) {
    e.preventDefault();
    showNextImage();
  } else if ((e.key === "z" || e.key === "Z") && !e.ctrlKey && !e.metaKey) {
    // Modifier-blind `z` would make Ctrl+Z and Ctrl+Shift+Z (which reports
    // `e.key === "Z"`) zoom instead of undo: the worst kind of collision,
    // because it does something visible and wrong.
    toggleZoomSnap();
  } else if (e.key === " " || e.key === "Spacebar") {
    e.preventDefault();
    if (!chromeHidden.value) {
      chromeHidden.value = true;
    }
  } else if (e.key === "s" || e.key === "S") {
    toggleSidebar();
  } else if (
    (e.key === "i" || e.key === "I") &&
    fullscreen.value &&
    !e.ctrlKey &&
    !e.metaKey &&
    !e.altKey
  ) {
    // The inspector, called back over the full-screen picture.
    fullscreenInspector.value = !fullscreenInspector.value;
  } else if (
    (e.key === "f" || e.key === "F") &&
    !e.repeat &&
    !e.ctrlKey &&
    !e.metaKey &&
    !e.altKey
  ) {
    void toggleFullscreen();
  } else if (e.key === "a" || e.key === "A") {
    if (addToSetControlRef.value?.lastUsedSet?.id) {
      e.preventDefault();
      addToSetControlRef.value.addToLastSet();
    }
  } else if (e.key === "[" || e.key === "]") {
    // One press is one 90° step, same as the toolbar buttons; two presses make
    // 180°. Unmodified only - Ctrl+[ / Cmd+[ is the browser's own Back on
    // several platforms, and a bracket that both navigated history and turned
    // the picture would be the worst kind of collision.
    if (!e.ctrlKey && !e.metaKey && !e.altKey && canRotateCurrent.value) {
      e.preventDefault();
      void rotateCurrentImage(e.key === "[" ? ROTATE_CCW : ROTATE_CW);
    }
  } else if (e.key === "t" || e.key === "T") {
    if (!isReadOnly.value) {
      e.preventDefault();
      beginTagEntry();
    }
  } else if ([1, 2, 3, 4, 5].includes(bareDigit(e))) {
    // A bare digit only: Ctrl+2 is not two stars.
    if (!isReadOnly.value) {
      if (image.value) setScore(bareDigit(e));
    }
  } else if (
    e.key.toLowerCase() === FAVOURITE_KEY &&
    !e.ctrlKey &&
    !e.metaKey &&
    !e.altKey
  ) {
    toggleFavourite();
  }
}

const isMobile = ref(false);
const MOBILE_BREAKPOINT = 900;
// Teleport target, found on mount (see `onMounted`); null in a test that
// mounts the lightbox on its own, which then draws it in place.
const lightboxHost = ref(null);
const overlayMainRef = ref(null);
const filmstripRef = ref(null);
const touchStart = ref({ x: 0, y: 0, time: 0 });
const touchLatest = ref({ x: 0, y: 0 });
const swipeHintVisible = ref(false);
let swipeHintTimer = null;
let touchTapConsumed = false;
let lastTouchEndTime = 0;

function updateViewportMetrics() {
  if (typeof window !== "undefined") {
    isMobile.value = window.innerWidth <= MOBILE_BREAKPOINT;
  }
}

function showSwipeHint() {
  if (!isMobile.value) return;
  swipeHintVisible.value = true;
  if (swipeHintTimer) {
    clearTimeout(swipeHintTimer);
  }
  swipeHintTimer = window.setTimeout(() => {
    swipeHintVisible.value = false;
  }, 3000);
}

// Where the last press started, so a click that ended a pan or a drag-out is
// never read as a click on the dark area.
let pressStart = null;
const PRESS_MOVE_TOLERANCE_PX = 5;

function noteRootPointerDown(event) {
  pressCarriedIn = false;
  pressStart = {
    x: event.clientX,
    y: event.clientY,
    touch: event.pointerType === "touch",
  };
}

function pressMoved(event) {
  if (!pressStart) return false;
  return (
    Math.hypot(event.clientX - pressStart.x, event.clientY - pressStart.y) >
    PRESS_MOVE_TOLERANCE_PX
  );
}

function notePressOrigin(event) {
  pressCarriedIn = !pressSeenSinceOpen && event.detail > 1;
  pressSeenSinceOpen = true;
}

function handleBackdropClick() {
  if (pressCarriedIn) return;
  // A right-click context menu open over the lightbox is dismissed by this same
  // click. Swallow it so click-away closes the MENU ONLY, never the lightbox.
  // The menu was still in the DOM at pointerdown time (captured in
  // handleOverlayPointerDown), even though it removes itself before this click.
  if (menuWasOpenOnPointerDown) {
    menuWasOpenOnPointerDown = false;
    return;
  }
  emit("close");
}

// Middle click anywhere closes, except where it means something else: Linux
// pastes into a text field with it, and a link opens in a new tab.
function middleClickKept(event) {
  const target = event?.target;
  return (
    event.button !== 1 ||
    !(target instanceof Element) ||
    isEditableElement(target) ||
    !!target.closest("a[href]")
  );
}

/** Chromium starts autoscroll on the middle press; the close needs it not to. */
function preventMiddleAutoscroll(event) {
  if (middleClickKept(event)) return;
  event.preventDefault();
}

function handleMiddleClick(event) {
  if (middleClickKept(event)) return;
  if (menuWasOpenOnPointerDown) return;
  event.preventDefault();
  emit("close");
}

// ── Media context menu (right-click / keyboard) ────────────────────────────
// Right-click over the media surface opens the custom overlay context menu
// (owned by ImageGrid, which scopes every action to this one picture). The
// sidebar panels and filmstrip are siblings of `.overlay-canvas`, so a
// right-click there is never seen here and keeps the native menu (copy / paste
// / spellcheck in the description and tag fields).
function buildContextMenuImage() {
  if (!image.value?.id) return null;
  const copy = copyAvailability();
  const video = isSupportedVideoFile(getOverlayFormat(image.value));
  return {
    ...image.value,
    format: getOverlayFormat(image.value),
    faces: Array.isArray(faceBboxes.value) ? faceBboxes.value : [],
    mediaKind: video ? "video" : "picture",
    stackPlace: overlayStackPlace.value,
    copyAvailable: copy.available,
    copyUnavailableReason: copy.reason,
  };
}

function handleMediaContextMenu(event) {
  const ctxImage = buildContextMenuImage();
  if (!ctxImage) return; // no image loaded → fall through to the native menu
  event.preventDefault();
  emit("request-context-menu", {
    clientX: event.clientX,
    clientY: event.clientY,
    image: ctxImage,
  });
}

function openContextMenuFromKeyboard() {
  const ctxImage = buildContextMenuImage();
  if (!ctxImage) return;
  // Focus the canvas first so the menu captures it as the invoker and returns
  // focus here on Escape. Anchor the menu over the centre of the media.
  const el = overlayCanvasRef.value;
  el?.focus();
  const rect = el?.getBoundingClientRect();
  const x = rect ? Math.round(rect.left + rect.width / 2) : 0;
  const y = rect ? Math.round(rect.top + rect.height / 2) : 0;
  emit("request-context-menu", { clientX: x, clientY: y, image: ctxImage });
}

function handleUserActivity() {
  if (chromeHidden.value) {
    chromeRevealTimestamp.value = Date.now();
  }

  chromeHidden.value = false;
}

function handleMouseActivity(event) {
  if (event) noteFullscreenPointer(event);
  if (chromeHidden.value) return; // mouse movement never reveals chrome
  if (Date.now() - lastTouchEndTime < 600) return;
  handleUserActivity();
}

function handleWheelActivity() {
  // Scroll always reveals chrome (deliberate interaction)
  handleUserActivity();
}

function handleOverlayPointerDown(event) {
  menuWasOpenOnPointerDown = !!(
    document.querySelector(".v-overlay--active") ||
    document.querySelector(".image-overlay .ate.open") ||
    // Our own right-click context menu (teleported to <body>). It closes itself
    // on this pointerdown, so it must be detected now, not at click time.
    document.querySelector(".image-ctx-menu") ||
    filmstripMenu.value
  );
  if (filmstripMenu.value && !event?.target?.closest?.(".filmstrip-ctx-menu")) {
    filmstripMenu.value = null;
  }
}

const OVERLAY_INTERACTIVE_SELECTOR =
  "button, a, input, select, textarea, label, summary, details";
const OVERLAY_INTERACTIVE_CONTAINER_SELECTOR =
  ".overlay-sidebar, .overlay-rail, .overlay-nav, .paint-toolbox, .paint-options";

/** A still mouse click on the canvas outside the picture's on-screen bounds.
 * Never true on a mis-measured rect: no media element, or one with no size
 * yet (still loading, a video before its metadata), is not a dark area. */
function isDarkAreaClick(event, target) {
  if (menuWasOpenOnPointerDown || paintActive.value || drawMode.value) {
    return false;
  }
  // Touch keeps tap-to-toggle-chrome: on a phone the letterbox is most of
  // the screen.
  if (pressStart?.touch || pressMoved(event)) return false;
  if (!target.closest(".overlay-canvas")) return false;
  if (
    target.closest(OVERLAY_INTERACTIVE_SELECTOR) ||
    target.closest(OVERLAY_INTERACTIVE_CONTAINER_SELECTOR)
  ) {
    return false;
  }
  const media = mediaInnerRef.value?.querySelector("img, video");
  const rect = media?.getBoundingClientRect?.();
  if (!rect || rect.width <= 0 || rect.height <= 0) return false;
  return (
    event.clientX < rect.left ||
    event.clientX > rect.right ||
    event.clientY < rect.top ||
    event.clientY > rect.bottom
  );
}

function handleOverlayClick(event) {
  if (pressCarriedIn) return;
  if (touchTapConsumed) {
    touchTapConsumed = false;
    return;
  }
  const target = event?.target;
  // A control whose own click re-rendered it away (Run edit becoming the
  // run's progress) is detached by now, so `closest` below finds nothing: it
  // was a control, not the picture.
  if (!target || !(target instanceof HTMLElement) || !target.isConnected) {
    handleUserActivity();
    return;
  }
  // Ahead of the chrome branches: the dark area closes whether the chrome is
  // shown or hidden.
  if (isDarkAreaClick(event, target)) {
    emit("close");
    return;
  }
  if (chromeHidden.value) {
    handleUserActivity();
    return;
  }
  if (Date.now() - chromeRevealTimestamp.value < 500) {
    return;
  }
  if (menuWasOpenOnPointerDown) {
    menuWasOpenOnPointerDown = false;
    return;
  }
  if (
    target.closest(OVERLAY_INTERACTIVE_SELECTOR) ||
    target.closest(OVERLAY_INTERACTIVE_CONTAINER_SELECTOR)
  ) {
    handleUserActivity();
    return;
  }
  // The Edit tab's tools live in the chrome. While they are out, any click
  // that gets past the paint input layer (a press with no tool held, a stroke
  // the layer did not take) must not hide the toolbox mid-edit; Space still can.
  if (paintActive.value) {
    handleUserActivity();
    return;
  }
  // In full screen there is no chrome left to hide: a tap does what pointing
  // at the top does, for touch.
  if (fullscreen.value) {
    if (pressStart?.touch) {
      fullscreenBarShown.value = true;
      scheduleFullscreenBarHide(FULLSCREEN_TAP_BAR_MS);
    }
    return;
  }
  chromeHidden.value = true;
}

/** T: the tag field, on Info, in a pane opened for it if the rail was shut.
 * A reader sitting on Recipe is brought back to Info rather than given an
 * invisible cursor. `beginAddTag` focuses after the next render, by which the
 * pane and the Info group are shown. */
function beginTagEntry() {
  openSidebar();
  sidebarTab.value = "info";
  tagsPanelRef.value?.beginAddTag();
}

// S: the reader's own toggle, kept like the toolbar's. In full screen it is
// the full-screen inspector's, which nothing remembers.
function toggleSidebar() {
  if (fullscreen.value) fullscreenInspector.value = !fullscreenInspector.value;
  else sidebarStore.toggleLightboxRail();
  handleUserActivity();
}

const edgeTabShown = computed(
  () => !!image.value && !inspectorShown.value && !isMobile.value,
);
/** What the closed inspector's edge tab names: the tab it reopens on. */
const edgeTabLabel = computed(
  () =>
    sidebarTabs.value.find((tab) => tab.value === sidebarTab.value)?.label ||
    "Info",
);

// The same act as S, so outside full screen it is remembered.
function openInspectorFromEdge() {
  toggleSidebar();
}

/** The pointer position relative to the zoom viewport (`.overlay-canvas`),
 * the anchor space every zoom change works in. */
function canvasCursorFromEvent(event) {
  const rect = overlayCanvasRef.value?.getBoundingClientRect?.();
  if (!rect || !Number.isFinite(event?.clientX)) return null;
  const pad = stagePadding();
  return {
    x: event.clientX - rect.left - pad.left,
    y: event.clientY - rect.top - pad.top,
  };
}

/** The stage's margin round the fitted picture (`.overlay-canvas` padding):
 * the zoom core measures, and anchors in, the box inside it. */
function stagePadding() {
  const canvas = overlayCanvasRef.value;
  const style = canvas ? window.getComputedStyle(canvas) : null;
  const px = (value) => parseFloat(value) || 0;
  return {
    left: px(style?.paddingLeft),
    top: px(style?.paddingTop),
    x: px(style?.paddingLeft) + px(style?.paddingRight),
    y: px(style?.paddingTop) + px(style?.paddingBottom),
  };
}

/** Z and the toolbar button: toggle between the snap stops, fit ↔ 100%,
 * centre-anchored. */
function toggleZoomSnap() {
  zoom.toggleSnap();
}

/** Double-click: the same fit ↔ 100% toggle, anchored at the click point. */
function onCanvasDblClick(event) {
  if (pressCarriedIn) return;
  const target = event?.target;
  if (target instanceof HTMLElement && target.closest(".overlay-nav")) {
    return;
  }
  zoom.toggleSnap(canvasCursorFromEvent(event));
}

function onPanStart(event) {
  if (drawMode.value) return;
  if (!isZoomed.value) return;
  event.preventDefault();
  isPanning.value = true;
  lastPointer.value = { x: event.clientX, y: event.clientY };
  if (event.currentTarget?.setPointerCapture) {
    event.currentTarget.setPointerCapture(event.pointerId);
  }
}

function onPanMove(event) {
  if (!isPanning.value || !isZoomed.value) return;
  const dx = event.clientX - lastPointer.value.x;
  const dy = event.clientY - lastPointer.value.y;
  zoom.panBy(dx, dy);
  lastPointer.value = { x: event.clientX, y: event.clientY };
}

function onPanEnd(event) {
  isPanning.value = false;
  try {
    event?.currentTarget?.releasePointerCapture(event.pointerId);
  } catch {
    /* pointer already released */
  }
}

function handleMediaDragStart(event) {
  if (isZoomed.value) {
    event.preventDefault();
    return;
  }
  const data = image.value;
  const id = data?.id;
  const dt = event.dataTransfer;
  if (id == null || !dt) return;
  try {
    // Internal-drag marker (same payload the grid thumbnails use): lets the
    // open image be dropped onto a sidebar character/set/project to assign it,
    // and stops the window-level handler from mistaking the drag for an
    // external file import. See isInternalImageDrag().
    setInternalDragPayload(dt, { type: "image-ids", imageIds: [id] });
    // Deterministic drag-out to the OS file manager. The browser's native
    // <img>/<video> drag only yields a real file for some formats (and never
    // for a <video>), which is why drag-out worked for some files and not
    // others. The DownloadURL hint tells Chromium/Electron to fetch and save
    // the actual media file wherever it is dropped, for every media type.
    // Format: "<mime>:<filename>:<absolute-url>".
    const url = getFullImageUrl(data);
    if (url) {
      const absoluteUrl = new URL(url, window.location.href).href;
      const ext = MediaFormat(data);
      const fallbackName = `${id}${ext ? `.${ext}` : ""}`;
      // original_file_name is user-controlled (set at import); sanitise it so a
      // path separator / colon / newline can't redirect or break the saved file
      // through the "<mime>:<filename>:<url>" DownloadURL hint.
      const filename = safeDownloadName(data.original_file_name, fallbackName);
      dt.setData(
        "DownloadURL",
        `${mediaMimeType(data)}:${filename}:${absoluteUrl}`,
      );
    }
  } catch (err) {
    console.error("[ERROR] Failed to set overlay drag data:", err);
  }
}

/** The canvas wheel. Ctrl/Meta+wheel always zooms (a trackpad pinch arrives
 * as Ctrl+wheel), Shift+wheel always browses, painting always zooms, and the
 * plain wheel follows the user's "Mouse wheel in the viewer" setting. Zoom is
 * the shared continuous cursor-anchored model: exponential in the normalized
 * delta, the point under the pointer stays put, wheel-out clamps at fit. */
function onCanvasWheel(event) {
  if (!open.value) return;
  handleUserActivity();
  const browse =
    !event.ctrlKey &&
    !event.metaKey &&
    (event.shiftKey ||
      (!paintActive.value && userPrefsStore.viewerWheelAction === "browse"));
  if (browse) {
    wheelBrowse(event);
    return;
  }
  zoom.wheelZoom(event, canvasCursorFromEvent(event));
}

// One mouse notch (~100px) is one picture; a trackpad swipe adds up instead of
// racing through twenty. The pause that ends a zoom gesture ends this one too.
// ponytail: fixed 100px threshold, tune if trackpads feel sluggish
const WHEEL_BROWSE_STEP_PX = 100;
let wheelBrowseAcc = 0;
let wheelBrowseLastTs = 0;

function wheelBrowse(event) {
  const now = Date.now();
  if (now - wheelBrowseLastTs > ZOOM_EXIT_GESTURE_GAP_MS) wheelBrowseAcc = 0;
  wheelBrowseLastTs = now;
  // Chromium delivers Shift+wheel as a horizontal deltaX with deltaY 0.
  const delta = normalizeWheelDelta({
    deltaMode: event.deltaMode,
    deltaY: event.deltaY || event.deltaX,
  });
  // A line- or page-mode event (Firefox's wheel) is one notch whatever its
  // size, so it is one step rather than a fraction of 100px.
  wheelBrowseAcc +=
    event.deltaMode === 0 ? delta : Math.sign(delta) * WHEEL_BROWSE_STEP_PX;
  if (Math.abs(wheelBrowseAcc) < WHEEL_BROWSE_STEP_PX) return;
  // Down or right is next. Same path as the arrow keys, so it inherits their
  // guards (the frozen sequence, stacks, the ends of the sequence).
  if (wheelBrowseAcc > 0) showNextImage();
  else showPrevImage();
  wheelBrowseAcc = 0;
}

// The transform transport (translate+scale) is load-bearing for the
// face-bbox overlays, draw-mode rectangle, and video, which all ride inside
// `.overlay-media`. Transform scale 1 IS fit: the un-transformed layout
// already renders the fitted image, so the CSS scale is scale/fitScale.
const mediaTransformStyle = computed(() => {
  const { x, y } = zoom.offset.value;
  return {
    transform: `translate(${x}px, ${y}px) scale(${zoom.transformScale.value})`,
  };
});

/** Above the fit floor: drag means pan (clamped), not drag-out. */
const isZoomed = zoom.aboveFit;

/** The button's live readout: whole percent of natural size - at fit this is
 * the computed fit percentage (e.g. "37%"), never the word "Fit". Empty only
 * until the image has measured; the reserved width absorbs it. */
const zoomButtonLabel = zoom.percentLabel;

const zoomButtonTitle = computed(() => {
  const pct = zoom.percentLabel.value;
  if (!pct) return "Zoom (Z)";
  return zoom.atFit.value
    ? `Zoom ${pct} (fit) - click for 100% (Z)`
    : `Zoom ${pct} - click to fit (Z)`;
});

// The filmstrip scrolls on its own, natively, as the grid does: the wheel and
// a trackpad move it by the pixel, and clicking a thumb leaves it where it is,
// so the second thumb stays second. It follows the picture on screen only when
// that picture would leave view (OverlayFilmstrip `reveal`), and every open
// starts centred, even on the picture it was closed on.
//
// A band under the stage (#2163): each thumb as tall as the band allows and as
// wide as its picture's own shape, clamped so a panorama does not fill the
// strip and a sliver can still be hit.
const FILMSTRIP_THUMB_H = 56; // OverlayFilmstrip's --filmstrip-thumb-h
const FILMSTRIP_GAP = 2;
const FILMSTRIP_MIN_W = 24;
const FILMSTRIP_MAX_W = 140;
const FILMSTRIP_RENDER_BUFFER = 20;
// The thumbs in view, as the strip last reported them.
const filmstripVisible = ref({ first: 0, last: 0 });
let filmstripPlaced = false;
let filmstripRevealPending = false;

const filmstripLayout = computed(() => {
  const images = filmstripImages.value;
  const offsets = new Array(images.length);
  const widths = new Array(images.length);
  let x = 0;
  let prevStackId = null;
  images.forEach((img, idx) => {
    const stackId = getPictureStackId(img);
    // Members of a stack opened here sit flush, so the run reads as one.
    const joined =
      !!stackId &&
      stackId === prevStackId &&
      overlayExpandedStackIds.value.has(stackId);
    if (idx > 0 && !joined) x += FILMSTRIP_GAP;
    offsets[idx] = x;
    widths[idx] = Math.min(
      FILMSTRIP_MAX_W,
      Math.max(
        FILMSTRIP_MIN_W,
        Math.round(FILMSTRIP_THUMB_H * displayedAspectRatio(img)),
      ),
    );
    x += widths[idx];
    prevStackId = stackId;
  });
  return { offsets, widths };
});

const filmstripCurrentIndex = computed(() =>
  image.value ? filmstripIndexById.value.get(String(image.value.id)) : undefined,
);

// "5 of 111" in the toolbar: the order ← and → walk, which counts a stack
// opened here member by member. The walk wraps, so one picture is all it
// takes to have nowhere to go.
const positionLabel = computed(() => {
  const idx = filmstripCurrentIndex.value;
  const total = filmstripImages.value.length;
  if (!(idx >= 0) || !total) return "";
  return `${(idx + 1).toLocaleString()} of ${total.toLocaleString()}`;
});
const canStep = computed(() => filmstripImages.value.length > 1);

function revealInFilmstrip() {
  // Once per tick (an open also moves the picture), and after the render so
  // the strip's spacer already counts every thumb.
  if (filmstripRevealPending) return;
  filmstripRevealPending = true;
  nextTick(() => {
    filmstripRevealPending = false;
    const idx = filmstripCurrentIndex.value;
    if (!(idx >= 0)) return;
    filmstripRef.value?.reveal?.(idx, { centre: !filmstripPlaced });
    filmstripPlaced = true;
  });
}

watch(filmstripCurrentIndex, revealInFilmstrip, { immediate: true });
watch(open, (value) => {
  if (!value) return;
  filmstripPlaced = false;
  revealInFilmstrip();
});

const filmstripCanvasData = computed(() => {
  const images = filmstripImages.value;
  const safeCurrentIndex = filmstripCurrentIndex.value ?? -1;
  if (!images.length || safeCurrentIndex === -1) {
    return { items: [], start: 0 };
  }

  // What is in view plus a buffer either side, so a thumb is loaded well
  // before it scrolls in.
  const lastIndex = images.length - 1;
  const first = Math.min(filmstripVisible.value.first, lastIndex);
  const last = Math.min(
    Math.max(first, filmstripVisible.value.last),
    lastIndex,
  );
  const canvasStart = Math.max(0, first - FILMSTRIP_RENDER_BUFFER);
  const canvasEnd = Math.min(lastIndex, last + FILMSTRIP_RENDER_BUFFER);

  const indices = [];
  for (let idx = canvasStart; idx <= canvasEnd; idx += 1) {
    indices.push(idx);
  }
  const items = indices.map((idx) => {
    const item = images[idx];
    const stackId = getPictureStackId(item);
    const prevItem = idx > 0 ? images[idx - 1] : null;
    const prevStackId = getPictureStackId(prevItem);
    const isStackExpanded = stackId
      ? overlayExpandedStackIds.value.has(stackId)
      : false;

    // Pre-compute all display fields for OverlayFilmstrip
    const thumbSrc = (() => {
      if (item.thumbnail) {
        const thumbnail = String(item.thumbnail);
        if (!thumbnail) return "";
        if (thumbnail.startsWith("http")) return appendShareToken(thumbnail);
        if (thumbnail.startsWith("/")) {
          return appendShareToken(
            backendUrl.value ? `${backendUrl.value}${thumbnail}` : thumbnail,
          );
        }
        return appendShareToken(thumbnail);
      }
      if (item.id != null && backendUrl.value) {
        return appendShareToken(
          `${backendUrl.value}/pictures/thumbnails/${item.id}.webp`,
        );
      }
      return "";
    })();
    const stackColor = getOverlayStackColor(item);
    const stackTileStyle = (() => {
      if (!isStackExpanded) return {};
      const color = applyStackBackgroundAlpha(stackColor);
      return color ? { "--filmstrip-stack-bg": color } : {};
    })();

    return {
      ...item,
      index: idx,
      isActive: idx === safeCurrentIndex,
      thumbSrc,
      isVideo: isSupportedVideoFile(getOverlayFormat(item)),
      stackBadgeVisible: getOverlayStackCount(item) > 1,
      isStackLead: stackId !== prevStackId,
      // Only in a stack opened in the filmstrip: the move handles need the
      // neighbours on screen.
      stackPlace: isStackExpanded ? getOverlayStackPlace(item) : null,
      stackBadgeTitle: (() => {
        const count = getOverlayStackCount(item);
        return count <= 1 ? "" : `Stack of ${count} images`;
      })(),
      problemBadgeVisible:
        showProblemIcon.value &&
        !!thumbSrc &&
        Array.isArray(item?.penalised_tags) &&
        item.penalised_tags.length > 0,
      problemTitle: (() => {
        const tags = Array.isArray(item?.penalised_tags)
          ? item.penalised_tags
          : [];
        return tags.length ? `Penalised tags: ${tags.join(", ")}` : "";
      })(),
      stackIconStyle: stackColor ? { color: stackColor } : {},
      stackTileStyle,
    };
  });

  return { items, start: canvasStart };
});

const filmstripCanvasWindow = computed(() => filmstripCanvasData.value.items);

// ── Boxes on demand ────────────────────────────────────────────────────────
//
// A box is drawn while the reader asks about what it outlines. Pointing at a
// face crop in Info draws that face's box; pointing at a tag the object
// detector found draws its box; holding Shift draws every box. The one switch
// is the toolbar's Segmentation boxes, which keeps the detection boxes on. Pointing at a face on the picture rings its
// crop in Info.
const FACE_CHIP_SIZE = 42;
const shiftHeld = ref(false);
const hoveredFaceIdx = ref(null);
const pictureFaceIdx = ref(null);
const hoveredTagLabel = ref(null);
// The pencil on Faces: the panel of person pickers, one per face.
const namingFaces = ref(false);
const facesPencilRef = ref(null);
const facesPanelRef = ref(null);

function closeFacesPanel() {
  // A press in the new-person dialog a face row opened lands outside the
  // panel; the panel stays so focus can return to that row's picker.
  if (createPersonOpen.value) return;
  namingFaces.value = false;
}

function drawFaceFromPanel() {
  namingFaces.value = false;
  beginDrawMode("face");
}
// Shift means "add" to the selection tools, so boxes stand down while they
// are out, as the face boxes always did.
const boxesAll = computed(() => shiftHeld.value && !paintActive.value);
// The toolbar's Segmentation boxes toggle: every detection box, left on.
const showDetections = ref(false);

/** The labels the object detector found, folded for matching against tags. */
const detectionLabels = computed(
  () =>
    new Set(
      detectionBboxes.value
        .map((det) => String(det?.label ?? "").trim().toLowerCase())
        .filter(Boolean),
    ),
);

function detectionMatchesTag(det, tag) {
  if (!tag) return false;
  return (
    String(det?.label ?? "").trim().toLowerCase() ===
    String(tag).trim().toLowerCase()
  );
}

/** The face under the pointer on the picture, whose crop Info rings. */
function onStagePointerMove(event) {
  const faces = faceBboxes.value;
  if (!faces.length || paintActive.value || drawMode.value || isPanning.value) {
    pictureFaceIdx.value = null;
    return;
  }
  const media = mediaInnerRef.value?.querySelector("img");
  const rect = media?.getBoundingClientRect?.();
  const inside =
    !!rect &&
    event.clientX >= rect.left &&
    event.clientX <= rect.right &&
    event.clientY >= rect.top &&
    event.clientY <= rect.bottom;
  const at = inside ? getDrawPoint(event) : null;
  const idx = at
    ? faces.findIndex(
        (face) =>
          isValidOverlayBBox(face.bbox) &&
          at.x >= face.bbox[0] &&
          at.x <= face.bbox[2] &&
          at.y >= face.bbox[1] &&
          at.y <= face.bbox[3],
      )
    : -1;
  pictureFaceIdx.value = idx >= 0 ? idx : null;
}

// A box asked about on one picture is not asked about on the next, nor once
// the crop or chip that asked has gone out of sight (no mouseleave comes then).
watch(
  () => [image.value?.id, inspectorShown.value, sidebarTab.value],
  () => {
    hoveredFaceIdx.value = null;
    pictureFaceIdx.value = null;
    hoveredTagLabel.value = null;
  },
);

function onShiftKeyup(event) {
  if (event.key === "Shift") shiftHeld.value = false;
}

function releaseShift() {
  shiftHeld.value = false;
}

const drawMode = ref(null);
const drawState = ref({
  active: false,
  startX: 0,
  startY: 0,
  currentX: 0,
  currentY: 0,
});
const drawSubmitInFlight = ref(false);

const drawModeLabel = computed(() => {
  if (drawMode.value === "face") return "face";
  return "";
});

function beginDrawMode(mode) {
  if (!mode) return;
  drawMode.value = mode;
  drawState.value = {
    active: false,
    startX: 0,
    startY: 0,
    currentX: 0,
    currentY: 0,
  };
}

function clearDrawMode() {
  drawMode.value = null;
  drawSubmitInFlight.value = false;
  drawState.value = {
    active: false,
    startX: 0,
    startY: 0,
    currentX: 0,
    currentY: 0,
  };
}

const imgRef = ref(null);
const videoRef = ref(null);
const overlayCanvasRef = ref(null);
const mediaInnerRef = ref(null);
const videoMeta = ref({ duration: null });
const videoError = ref(null); // set to MediaError when browser can't play the video
// Derive the video src from id + format only - deliberately excludes pixel_sha
// so the URL stays stable across metadata merges (which would otherwise abort
// the in-progress download). Returns '' while format is not yet known, which
// also keeps the <video> element from being created prematurely via v-if.
const videoSrc = computed(() => {
  const id = image.value?.id;
  const fmt = image.value?.format;
  if (!id || !fmt || !isSupportedVideoFile(`file.${fmt}`)) return "";
  return appendShareToken(
    `${backendUrl.value}/pictures/${id}.${fmt.toLowerCase()}`,
  );
});
// A cold overlay route initially knows only the picture id. An extension-less
// `/pictures/{id}` is the JSON detail endpoint, not media: mounting it as an
// <img> yields a successful HTTP response followed by a decode error. Wait for
// the metadata/grid record to provide the real format before creating the
// native image element.
function buildFullImageSrc(data) {
  const id = data?.id;
  const fmt = MediaFormat(data);
  if (!id || !fmt || isSupportedVideoFile(`file.${fmt}`)) return "";
  return getFullImageUrl(data);
}

function knownOrientation(value) {
  if (value === null || value === undefined || value === "") return null;
  const orientation = Number(value);
  return Number.isFinite(orientation) ? orientation : null;
}

// A cold-opened row can still arrive with `orientation: null` while the
// backfill catches up. In that case metadata may later confirm a known
// orientation for the same bytes; switching `fullImageSrc` at that moment would
// remount the keyed <img> and flash blank. Keep rendering the first URL until
// orientation moves between two known values (the actual in-place rotate case).
const pinnedOrientation = ref(undefined);
watch(
  () => [image.value?.id, image.value?.orientation],
  ([id, orientation], [previousId, previousOrientation] = []) => {
    if (!id) {
      pinnedOrientation.value = undefined;
      return;
    }
    const currentKnown = knownOrientation(orientation);
    const previousKnown = knownOrientation(previousOrientation);
    if (id !== previousId) {
      const hasInitialUrl = Boolean(buildFullImageSrc(image.value));
      pinnedOrientation.value =
        currentKnown === null && hasInitialUrl ? null : undefined;
      return;
    }
    if (
      pinnedOrientation.value === null &&
      currentKnown !== null &&
      (isRotateMetadataInFlight(String(id)) ||
        (previousKnown !== null && currentKnown !== previousKnown))
    ) {
      pinnedOrientation.value = undefined;
    }
  },
  { immediate: true },
);
const fullImageSrc = computed(() => {
  if (!image.value) return "";
  if (pinnedOrientation.value === null) {
    return buildFullImageSrc({ ...image.value, orientation: null });
  }
  return buildFullImageSrc(image.value);
});
const overlayDims = ref({
  width: 1,
  height: 1,
  naturalWidth: 1,
  naturalHeight: 1,
  offsetX: 0,
  offsetY: 0,
});
const overlayReady = computed(() => {
  const dims = overlayDims.value;
  return (
    Number.isFinite(dims.width) &&
    Number.isFinite(dims.height) &&
    Number.isFinite(dims.naturalWidth) &&
    Number.isFinite(dims.naturalHeight) &&
    dims.width > 1 &&
    dims.height > 1 &&
    dims.naturalWidth > 1 &&
    dims.naturalHeight > 1
  );
});

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function getDrawPoint(event) {
  if (!overlayReady.value) return null;
  const innerEl = mediaInnerRef.value;
  if (!innerEl) return null;
  const rect = innerEl.getBoundingClientRect();
  const dims = overlayDims.value;
  // The bounding rect is the TRANSFORMED box (pan + continuous scale), while
  // dims are layout-space, so the cursor divides through the CSS scale before
  // the layout-space mapping. At fit the scale is 1 and this is a no-op.
  const cssScale = zoom.transformScale.value || 1;
  const localX = (event.clientX - rect.left) / cssScale - (dims.offsetX || 0);
  const localY = (event.clientY - rect.top) / cssScale - (dims.offsetY || 0);
  const clampedX = clamp(localX, 0, dims.width);
  const clampedY = clamp(localY, 0, dims.height);
  const imgX = (clampedX * dims.naturalWidth) / dims.width;
  const imgY = (clampedY * dims.naturalHeight) / dims.height;
  return {
    x: clamp(imgX, 0, dims.naturalWidth),
    y: clamp(imgY, 0, dims.naturalHeight),
  };
}

const drawRectStyle = computed(() => {
  if (!drawMode.value) return null;
  const state = drawState.value;
  if (!state.active) return null;
  const dims = overlayDims.value;
  const x1 = Math.min(state.startX, state.currentX);
  const y1 = Math.min(state.startY, state.currentY);
  const x2 = Math.max(state.startX, state.currentX);
  const y2 = Math.max(state.startY, state.currentY);
  const left = (dims.offsetX || 0) + (x1 * dims.width) / dims.naturalWidth;
  const top = (dims.offsetY || 0) + (y1 * dims.height) / dims.naturalHeight;
  const width = ((x2 - x1) * dims.width) / dims.naturalWidth;
  const height = ((y2 - y1) * dims.height) / dims.naturalHeight;
  return {
    left: `${left || 0}px`,
    top: `${top || 0}px`,
    width: `${width || 0}px`,
    height: `${height || 0}px`,
  };
});

function onDrawStart(event) {
  if (!drawMode.value) return;
  const point = getDrawPoint(event);
  if (!point) return;
  drawState.value = {
    active: true,
    startX: point.x,
    startY: point.y,
    currentX: point.x,
    currentY: point.y,
  };
  if (event.currentTarget?.setPointerCapture) {
    event.currentTarget.setPointerCapture(event.pointerId);
  }
}

function onDrawMove(event) {
  if (!drawMode.value || !drawState.value.active) return;
  const point = getDrawPoint(event);
  if (!point) return;
  drawState.value = {
    ...drawState.value,
    currentX: point.x,
    currentY: point.y,
  };
}

async function onDrawEnd(event) {
  if (!drawMode.value || !drawState.value.active) return;
  if (drawSubmitInFlight.value) return;
  drawSubmitInFlight.value = true;
  const state = drawState.value;
  const x1 = Math.min(state.startX, state.currentX);
  const y1 = Math.min(state.startY, state.currentY);
  const x2 = Math.max(state.startX, state.currentX);
  const y2 = Math.max(state.startY, state.currentY);
  drawState.value = { ...drawState.value, active: false };
  try {
    event?.currentTarget?.releasePointerCapture(event.pointerId);
  } catch {
    /* pointer already released */
  }
  if (Math.abs(x2 - x1) < 5 || Math.abs(y2 - y1) < 5) {
    clearDrawMode();
    return;
  }
  if (!image.value?.id || !backendUrl.value) {
    clearDrawMode();
    return;
  }
  const capturedImageId = image.value.id;
  const capturedBackendUrl = backendUrl.value;
  const payload = { bbox: [x1, y1, x2, y2], frame_index: 0 };
  try {
    if (drawMode.value === "face") {
      await addPictureFace(capturedImageId, payload, {
        baseUrl: capturedBackendUrl,
      });
      await fetchFaceBboxes(capturedImageId);
      emit("overlay-change", {
        imageId: capturedImageId,
        fields: { faces: true },
      });
    }
  } catch (e) {
    console.error("Failed to create box", e);
    noticeStore.error(
      `Couldn't create that ${drawModeLabel.value} box. ${errorDetail(e) || e?.message || "Please try again."}`,
      { key: "overlay-create-box" },
    );
  } finally {
    clearDrawMode();
  }
}

function onDrawCancel(event) {
  try {
    event?.currentTarget?.releasePointerCapture(event.pointerId);
  } catch {
    /* pointer already released */
  }
  clearDrawMode();
}
// ── Paint: the Edit tab's selection and crop ───────────────────────────────
//
// Here and not with the Edit block above: it reads `overlayDims`. The tools
// are out only while the Edit tab is the one on screen, so every other tab
// keeps the lightbox's own pointer and keys.
const paint = usePaintSelection({
  pictureId: computed(() => image.value?.id ?? null),
  dims: overlayDims,
});
const paintActive = computed(
  () =>
    editTabShown.value && inspectorShown.value && sidebarTab.value === "edit",
);
// The floating options strip, for the tools whose options act on the picture.
const paintOptionsShown = computed(
  () => MODE_TOOLS.has(paint.tool.value) || paint.tool.value === "crop",
);

// The pictures *Apply to* offers, from the grid's *Edit…* on a selection.
const applyToIds = ref([]);
watch(editRequest, (request) => {
  const ids = (request?.pictureIds || []).map(Number).filter(Boolean);
  applyToIds.value = ids.length > 1 ? ids : [];
  if (!request) return;
  openSidebar();
  chosenSidebarTab.value = "edit";
}, { immediate: true });
watch(open, (isOpen) => {
  if (isOpen) return;
  applyToIds.value = [];
  compare.value = null;
  filmstripPicks.value = [];
});

// The largest stored face, which Square on face frames.
const cropFramingsHere = computed(() => {
  const { naturalWidth, naturalHeight } = overlayDims.value;
  const area = (box) => (box[2] - box[0]) * (box[3] - box[1]);
  const face = faceBboxes.value
    .map((row) => row.bbox)
    .reduce((best, box) => (!best || area(box) > area(best) ? box : best), null);
  return cropFramings(naturalWidth, naturalHeight, face);
});

/** Crop runs as the built-in `crop` plugin: a new picture in this stack. */
function applyCrop(box) {
  if (!image.value?.id || !box) return;
  const [left, top, width, height] = box;
  emit("run-plugin", {
    pluginName: "crop",
    pictureIds: [image.value.id],
    parameters: { left, top, width, height },
    stack: true,
  });
  paint.cropBox.value = null;
  paint.setTool(null);
}

function paintPoint(event) {
  const at = getDrawPoint(event);
  if (!at) return null;
  const { naturalWidth, naturalHeight } = overlayDims.value;
  return { x: at.x / naturalWidth, y: at.y / naturalHeight };
}

function releasePaintPointer(event) {
  const target = event?.currentTarget;
  if (target?.hasPointerCapture?.(event.pointerId)) {
    target.releasePointerCapture(event.pointerId);
  }
}

function onPaintStart(event) {
  if (event.button !== 0) return;
  const at = paintPoint(event);
  if (!at) return;
  paint.begin(at, { shift: event.shiftKey, alt: event.altKey });
  event.currentTarget?.setPointerCapture?.(event.pointerId);
}

function onPaintMove(event) {
  if (!paint.draft.value) return;
  const at = paintPoint(event);
  if (at) paint.move(at);
}

function onPaintEnd(event) {
  releasePaintPointer(event);
  paint.end();
}

function onPaintCancel(event) {
  releasePaintPointer(event);
  paint.cancel();
}

const PAINT_KEYS = new Map(
  PAINT_TOOLS.map((tool) => [tool.key.toLowerCase(), tool.id]),
);

/** A Paint tool's key, I to invert, or − / + for the size of the brush or
 * eraser in hand (= is + without Shift). True when the key was the toolbox's. */
function handlePaintKey(key) {
  const lower = String(key || "").toLowerCase();
  if (SIZED_TOOLS.has(paint.tool.value) && ["-", "+", "="].includes(lower)) {
    paint.stepBrushSize(lower === "-" ? -1 : 1);
    return true;
  }
  // Without a ComfyUI nothing reads a selection: only crop is a tool here.
  const keyTool = PAINT_KEYS.get(lower);
  if (keyTool && keyTool !== "crop" && !comfyuiConfigured.value) return false;
  if (PAINT_KEYS.has(lower)) {
    paint.tool.value = PAINT_KEYS.get(lower);
    return true;
  }
  if (lower === "i" && paint.hasSelection.value) {
    paint.invert();
    return true;
  }
  return false;
}

// ── Before / after: the original over an edit's result ─────────────────────
// Set by *Show it* (or a run's own result) when the lightbox steps from the
// original to what was made from it, and shown while that result is on screen.
const compare = ref(null);
const comparePosition = ref(50);
const compareShown = computed(
  () =>
    !!compare.value &&
    overlayReady.value &&
    String(image.value?.id) === String(compare.value.resultId),
);

// The cut follows the pointer anywhere on the picture. A native range spread
// over the whole picture only answers a press on its thumb in some browsers;
// it stays underneath for the keyboard.
function compareAt(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  if (!rect.width) return;
  const at = ((event.clientX - rect.left) / rect.width) * 100;
  comparePosition.value = Math.round(Math.min(100, Math.max(0, at)));
}

function onCompareStart(event) {
  if (event.button !== 0) return;
  event.currentTarget.focus?.();
  event.currentTarget.setPointerCapture?.(event.pointerId);
  compareAt(event);
}

function onCompareMove(event) {
  if (event.currentTarget.hasPointerCapture?.(event.pointerId)) compareAt(event);
}

function onCompareEnd(event) {
  releasePaintPointer(event);
}

// Back to the result alone; the keyboard goes back to the picture.
function closeCompare() {
  compare.value = null;
  overlayCanvasRef.value?.focus?.();
}

const COMPARE_SIDES = ["before", "after"];

function compareLabel(side) {
  const current = compare.value || {};
  return side === "before"
    ? current.beforeLabel || "Before"
    : current.afterLabel || "After";
}

// Out of the comparison onto one side: the picture under it (after) or the
// one laid over it (before).
function leaveCompare(side) {
  const beforeId = compare.value?.beforeId;
  closeCompare();
  if (side === "before" && beforeId) setOverlayImageById(beforeId);
}

/** A picture's row, from the lightbox's lists or else read by id. */
async function pictureRowById(id) {
  const key = String(id);
  const known = allImageById.value.get(key) || filmstripImageById.value.get(key);
  if (known) return known;
  try {
    const rows = await listPicturesByIds([key], { fields: "grid" });
    return Array.isArray(rows) ? rows[0] || null : null;
  } catch (err) {
    console.warn(`Could not read picture ${key} to compare it:`, err);
    return null;
  }
}

/**
 * Lay *beforeId* over *afterId*, cut at the slider, and show *afterId*. Any
 * two pictures: picked in the grid or the filmstrip. They are A and B, the
 * first picked on the left.
 */
async function startCompare(beforeId, afterId) {
  const [before, after] = await Promise.all([
    pictureRowById(beforeId),
    pictureRowById(afterId),
  ]);
  // Both read now: a pick the button could only guess at may be a video.
  const refusal = before && after ? compareBlockReason([before, after]) : null;
  if (refusal) {
    noticeStore.error(refusal, { key: "compare-start" });
    return false;
  }
  const beforeSrc = before ? buildFullImageSrc(before) : "";
  if (!beforeSrc) {
    noticeStore.error("Couldn't open the first picture to compare.", {
      key: "compare-start",
    });
    return false;
  }
  compare.value = {
    resultId: String(afterId),
    beforeId: String(beforeId),
    beforeSrc,
    beforeLabel: "A",
    afterLabel: "B",
  };
  comparePosition.value = 50;
  // From the filmstrip; the grid opens the lightbox on it itself.
  if (open.value && String(image.value?.id) !== String(afterId)) {
    setOverlayImageById(afterId);
  }
  return true;
}

watch(
  compareRequest,
  (request) => {
    if (request?.beforeId == null || request?.afterId == null) return;
    startCompare(request.beforeId, request.afterId);
  },
  { immediate: true },
);

// ── Filmstrip picks: Ctrl/Cmd-click, for Compare ─────────────────────────
// Ids, in the order picked, two at most: a third pick drops the oldest. The
// first Ctrl-click picks the picture on screen too, so one Ctrl-click on
// another thumb makes a pair.
const filmstripPicks = ref([]);

function onFilmstripPick(item) {
  const id = item?.id == null ? "" : String(item.id);
  if (!id) return;
  let picks = filmstripPicks.value;
  const current = image.value?.id == null ? "" : String(image.value.id);
  if (!picks.length && current && current !== id) picks = [current];
  filmstripPicks.value = picks.includes(id)
    ? picks.filter((pick) => pick !== id)
    : [...picks, id].slice(-2);
}

const filmstripCompareReason = computed(() => {
  if (filmstripPicks.value.length !== 2) {
    return "Ctrl-click a picture in the filmstrip to compare it with this one";
  }
  // A pick whose row is not in the lightbox's lists is taken for a picture
  // here; `startCompare` reads both by id and refuses a video there.
  const rows = filmstripPicks.value.map(
    (id) =>
      filmstripImageById.value.get(id) ||
      allImageById.value.get(id) || { id, format: "png" },
  );
  return compareBlockReason(rows);
});

async function compareFilmstripPicks() {
  if (filmstripCompareReason.value) return;
  const [beforeId, afterId] = filmstripPicks.value;
  // Kept on a refusal (a video among them), so a pick can be swapped out.
  if (await startCompare(beforeId, afterId)) filmstripPicks.value = [];
}

// The filmstrip's right-click menu: `{x, y, invoker}` while open.
const filmstripMenu = ref(null);
const filmstripMenuRef = ref(null);
// Room for the one row, so a thumb at the bottom keeps its menu on screen.
const FILMSTRIP_MENU_ROOM_PX = 56;

function onFilmstripContextMenu(item, event) {
  if (item?.id == null) return;
  // Right-click picks the thumb, as a Ctrl-click does: one right-click on
  // another picture makes a pair with the one on screen.
  if (!filmstripPicks.value.includes(String(item.id))) onFilmstripPick(item);
  filmstripMenu.value = {
    x: event.clientX,
    y: Math.min(event.clientY, window.innerHeight - FILMSTRIP_MENU_ROOM_PX),
    invoker: event.currentTarget,
  };
  nextTick(() => filmstripMenuRef.value?.focus());
}

function closeFilmstripMenu() {
  const invoker = filmstripMenu.value?.invoker;
  filmstripMenu.value = null;
  invoker?.focus?.();
}

function compareFromFilmstripMenu() {
  filmstripMenu.value = null;
  compareFilmstripPicks();
}

// The stage resizes with the window and when the rail opens or shuts beside
// it (#2163): re-measure, so the fit and the zoom readout follow. Watched
// rather than observed on mount: the stage only exists while the lightbox is
// open.
let overlayResizeObserver = null;
watch(overlayMainRef, (el, oldEl) => {
  if (oldEl) overlayResizeObserver?.unobserve(oldEl);
  if (el) overlayResizeObserver?.observe(el);
});

function scheduleOverlayDimsUpdate() {
  nextTick(() => {
    requestAnimationFrame(() => {
      requestAnimationFrame(updateOverlayDims);
    });
  });
}

function updateOverlayDims() {
  if (imgRef.value) {
    const rect = imgRef.value.getBoundingClientRect();
    const width = imgRef.value.clientWidth || rect.width || 1;
    const height = imgRef.value.clientHeight || rect.height || 1;
    overlayDims.value.width = width;
    overlayDims.value.height = height;
    overlayDims.value.naturalWidth = imgRef.value.naturalWidth;
    overlayDims.value.naturalHeight = imgRef.value.naturalHeight;
    overlayDims.value.offsetX = imgRef.value.offsetLeft || 0;
    overlayDims.value.offsetY = imgRef.value.offsetTop || 0;
  } else if (videoRef.value) {
    const rect = videoRef.value.getBoundingClientRect();
    const width = videoRef.value.clientWidth || rect.width || 1;
    const height = videoRef.value.clientHeight || rect.height || 1;
    overlayDims.value.width = width;
    overlayDims.value.height = height;
    overlayDims.value.naturalWidth = videoRef.value.videoWidth;
    overlayDims.value.naturalHeight = videoRef.value.videoHeight;
    overlayDims.value.offsetX = videoRef.value.offsetLeft || 0;
    overlayDims.value.offsetY = videoRef.value.offsetTop || 0;
    const duration = Number.isFinite(videoRef.value.duration)
      ? videoRef.value.duration
      : null;
    videoMeta.value = { duration };
  } else {
    videoMeta.value = { duration: null };
  }
  syncZoomMeasurements();
}

/** Feed the zoom core its fit measurement whenever the media or the viewport
 * re-measures: entry lands at fit, a resize keeps a fit-parked scale on the
 * new fit (the button percentage follows), a navigation keeps the scale and
 * re-clamps it (and the pan) to the new image's floor. */
function syncZoomMeasurements() {
  if (!overlayReady.value) return;
  const canvas = overlayCanvasRef.value;
  const pad = stagePadding();
  const containerWidth = (canvas?.clientWidth || 0) - pad.x;
  const containerHeight = (canvas?.clientHeight || 0) - pad.y;
  if (containerWidth <= 1 || containerHeight <= 1) return;
  zoom.setMeasurements({
    containerWidth,
    containerHeight,
    naturalWidth: overlayDims.value.naturalWidth,
    naturalHeight: overlayDims.value.naturalHeight,
  });
}

watch(image, () => scheduleOverlayDimsUpdate());

// Record the URL that failed rather than a picture-wide boolean. Metadata and
// pixel updates can revise the media URL without changing the picture id; an
// error from the superseded URL must not hide the replacement image.
const fullImageErrorSrc = ref("");
const fullImageError = computed(
  () =>
    Boolean(fullImageSrc.value) &&
    fullImageErrorSrc.value === fullImageSrc.value,
);

function mediaEventSrc(event) {
  return event?.target?.getAttribute?.("src") || "";
}

function handleFullImageLoad(event) {
  // Ignore a late event from the element replaced by a reactive src change.
  if (mediaEventSrc(event) !== fullImageSrc.value) return;
  fullImageErrorSrc.value = "";
  updateOverlayDims();
}

function handleFullImageError(event) {
  const failedSrc = mediaEventSrc(event);
  if (!failedSrc || failedSrc !== fullImageSrc.value) return;
  console.warn("Full image load error for", event?.target?.src || failedSrc);
  fullImageErrorSrc.value = failedSrc;
}

function handleVideoError(event) {
  const err = event.target?.error;
  const code = err?.code ?? -1;
  const msg = err?.message ?? String(event);
  // MEDIA_ERR_SRC_NOT_SUPPORTED (code 4) is the most common - browser doesn't
  // support the container/codec (e.g. Firefox + .mov/ProRes).
  console.warn(`Video load error (code ${code}): ${msg}`);
  videoError.value = err ?? { code, message: msg };
}

onMounted(() => {
  updateViewportMetrics();
  window.addEventListener("resize", updateViewportMetrics);
  window.addEventListener("keydown", handleKeydown);
  window.addEventListener("keyup", onShiftKeyup);
  window.addEventListener("blur", releaseShift);
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("keydown", onCreatePersonKeydownCapture, true);
  window.addEventListener("pointerdown", handleOverlayPointerDown, true);
  // Mounted hooks run once App.vue's tree is in the document, so the host is
  // findable here and not during setup.
  lightboxHost.value = document.getElementById(LIGHTBOX_HOST_ID);
  if (typeof ResizeObserver !== "undefined") {
    overlayResizeObserver = new ResizeObserver(() => {
      scheduleOverlayDimsUpdate();
    });
    if (overlayMainRef.value) overlayResizeObserver.observe(overlayMainRef.value);
  }
});
onUnmounted(() => {
  window.removeEventListener("resize", updateViewportMetrics);
  window.removeEventListener("keydown", handleKeydown);
  window.removeEventListener("keyup", onShiftKeyup);
  window.removeEventListener("blur", releaseShift);
  document.removeEventListener("fullscreenchange", onFullscreenChange);
  leaveFullscreen();
  document.documentElement.classList.remove(FULLSCREEN_CLASS);
  clearTimeout(fullscreenBarTimer);
  clearTimeout(fullscreenNoticeTimer);
  clearTimeout(fullscreenIdleTimer);
  document.removeEventListener("keydown", onCreatePersonKeydownCapture, true);
  window.removeEventListener("pointerdown", handleOverlayPointerDown, true);
  if (overlayResizeObserver) {
    overlayResizeObserver.disconnect();
    overlayResizeObserver = null;
  }
  if (swipeHintTimer) {
    clearTimeout(swipeHintTimer);
    swipeHintTimer = null;
  }
  clearCharacterThumbnails();
  closeFallbackSaveDialog();
});

watch(open, (isOpen) => {
  if (!isOpen) {
    swipeHintVisible.value = false;
    if (swipeHintTimer) {
      clearTimeout(swipeHintTimer);
      swipeHintTimer = null;
    }
    return;
  }
  showSwipeHint();
  handleUserActivity();
});

function onTouchStart(event) {
  if (!isMobile.value) return;
  const touch = event.touches?.[0];
  if (!touch) return;
  touchStart.value = {
    x: touch.clientX,
    y: touch.clientY,
    time: Date.now(),
  };
  touchLatest.value = { x: touch.clientX, y: touch.clientY };
}

function onTouchMove(event) {
  if (!isMobile.value) return;
  const touch = event.touches?.[0];
  if (!touch) return;
  touchLatest.value = { x: touch.clientX, y: touch.clientY };
  handleUserActivity();
}

function onTouchEnd(event) {
  if (!isMobile.value) return;
  const dx = touchLatest.value.x - touchStart.value.x;
  const dy = touchLatest.value.y - touchStart.value.y;
  const absX = Math.abs(dx);
  const absY = Math.abs(dy);
  const elapsed = Date.now() - touchStart.value.time;
  const swipeThreshold = 50;
  const maxVertical = 80;
  const maxTime = 600;
  const tapThreshold = 10;

  if (absX >= swipeThreshold && absY <= maxVertical && elapsed <= maxTime) {
    if (dx > 0) {
      showPrevImage();
    } else {
      showNextImage();
    }
    return;
  }

  // Tap: toggle chrome visibility
  if (absX < tapThreshold && absY < tapThreshold) {
    lastTouchEndTime = Date.now();
    touchTapConsumed = true;
    const target = event?.changedTouches?.[0]
      ? document.elementFromPoint(
          event.changedTouches[0].clientX,
          event.changedTouches[0].clientY,
        )
      : null;
    const interactiveSelector =
      "button, a, input, select, textarea, label, summary, details";
    const interactiveContainerSelector =
      ".overlay-sidebar, .overlay-rail, .overlay-nav, .paint-toolbox, .paint-options";
    if (
      target &&
      (target.closest(interactiveSelector) ||
        target.closest(interactiveContainerSelector))
    ) {
      handleUserActivity();
      return;
    }
    // As a click: the Edit tab keeps its chrome.
    if (chromeHidden.value || paintActive.value) {
      handleUserActivity();
    } else {
      chromeHidden.value = true;
    }
  }
}

const faceBboxes = ref([]);
const detectionBboxes = ref([]);
const characters = ref([]);
const charactersLoading = ref(false);
const characterThumbnails = ref({});
let characterThumbnailEpoch = 0;
const FACE_THUMB_BASE = 34;
const FACE_THUMB_MIN = 28;
const FACE_THUMB_MAX = 60;
let metadataRequestId = 0;
let faceBboxesRequestId = 0;
let detectionBboxesRequestId = 0;
let comfyWorkflowRequestId = 0;

async function fetchComfyWorkflow(imageId) {
  if (!imageId || !backendUrl.value) return;
  // Cleared before the request, not after it. This component is not rebuilt as
  // the lightbox walks the filmstrip, so leaving the last picture's recipe in
  // place means the Recipe tab reads picture N-1's prompt, models and seed
  // under picture N until the response lands - and, for a picture with no
  // workflow, until a 404 that never replaces it.
  comfyMetadata.value = null;
  const requestId = (comfyWorkflowRequestId += 1);
  const requestedImageId = imageId;
  try {
    const data = await getPictureRecipe(imageId, { preflight: false });
    if (comfyWorkflowRequestId !== requestId) return;
    if (!image.value || image.value.id !== requestedImageId) {
      // The picture went away under the read. `comfyMetadata` was cleared
      // before it started, so leaving the tab shown would leave it shown over
      // nothing until some later read lands.
      recipeTabShown.value = false;
      return;
    }
    // `reason: "no_prompt_chunk"` is the recipe read's honest "this picture was
    // not generated", not an error - and unlike the workflow read it is a 200,
    // so the Recipe tab's empty state comes from here rather than from a 404.
    comfyMetadata.value =
      data && data.reason !== "no_prompt_chunk"
        ? {
            source: data.source || "comfyui",
            // Whether this recipe can be REPLAYED, which is not the same as
            // whether it can be read: an A1111 picture answers here in full
            // and is still not a graph ComfyUI could be handed.
            available: data.available,
            reason: data.reason || null,
            summary: data.summary,
            positive_prompt: data.positive_prompt || null,
            negativePrompt: data.negative_prompt || null,
            // The seed as text: a 64-bit one loses digits as a JS number.
            seedText: data.seed_text || null,
            // `model_slots` carries the strengths and the shelf rows that the
            // flat `models` name list cannot; `inputs` is the resolution lock.
            modelSlots: data.model_slots || [],
            settings: data.settings || {},
            inputs: data.inputs || [],
            topologyHash: data.topology_hash || null,
            // Why the editor graph could not be rebuilt into something
            // runnable. Empty for every other answer.
            conversionProblems: data.conversion_problems || [],
            // The workflow this picture's variant is in. The Recipe tab
            // needs it to read the workflow's saved recipes and to file a new
            // one under it (v1.12 F6); null when the hub has not keyed this
            // picture.
            workflowId: data.workflow_id || null,
            // The plain file names, which is the form saved-recipe credit
            // matches on - `model_slots` carries strengths the match ignores.
            loraNames: data.loras || [],
          }
        : null;
    recipeTabShown.value = comfyMetadata.value !== null;
  } catch (e) {
    // The recipe read answers 200 for a picture with no recipe, so a 404 here
    // means the picture or its file is gone, which is worth a line.
    console.error("Failed to fetch the picture's recipe:", e);
    if (comfyWorkflowRequestId !== requestId) return;
    comfyMetadata.value = null;
    recipeTabShown.value = false;
  }
}

async function fetchOverlayMetadata(imageId) {
  if (!imageId || !backendUrl.value) return;
  const requestId = (metadataRequestId += 1);
  try {
    const data = await getPictureMetadata(imageId, {
      smartScore: true,
      baseUrl: backendUrl.value,
    });
    if (metadataRequestId !== requestId) return;
    if (!image.value || image.value.id !== imageId) return;
    if (!data || Array.isArray(data)) return;
    const merged = { ...data, ...image.value };
    // The picture's own BYTES, where the server is unconditionally
    // authoritative and this component never holds an optimistic value. The
    // local-wins default above is right for everything the overlay can edit and
    // wrong for these: `frame_count` goes stale on an in-place edit, and
    // `orientation` is what `mediaVersion` builds the cache-buster from, so
    // keeping a stale copy leaves `fullImageSrc` pointing at the file the
    // `<img>` has already decoded. An in-place rotate moves it
    // and nothing else, which is exactly the case a local-wins merge would
    // swallow whole.
    for (const field of ["pixel_sha", "orientation", "frame_count"]) {
      if (data[field] !== undefined && data[field] !== null) {
        merged[field] = data[field];
      }
    }
    const existingSmartScore =
      typeof image.value?.smartScore === "number"
        ? image.value.smartScore
        : typeof image.value?.smart_score === "number"
          ? image.value.smart_score
          : null;
    if (Object.prototype.hasOwnProperty.call(data, "smartScore")) {
      // Prefer the server's freshly-committed value. After a tag or penalised-tag
      // settings edit the backend NULLs the cached score and recomputes, so an
      // authoritative NON-NULL fetch is the corrected number and MUST replace the
      // stale value currently shown (the panel used to keep the old value here,
      // which is why the score stayed stale until a full reload).
      // Only fall back to the currently-displayed value when the fetch returns
      // null/absent: the brief post-invalidation window before the recompute
      // lands, and grid-sourced images that don't carry a smartScore at all - so
      // we never flash "unscored" during the transient.
      const freshSmartScore = data.smartScore;
      merged.smartScore =
        freshSmartScore !== null && freshSmartScore !== undefined
          ? freshSmartScore
          : existingSmartScore;
    }
    const dataTags = getTagList(data.tags);
    if (data.tags !== undefined) {
      merged.tags = dedupeTagList(dataTags);
    }
    if (data.description !== undefined) {
      // The server is authoritative for the description, same reasoning as
      // smartScore above: this fetch is how an undo/redo (whose WS
      // descriptions_changed event triggers it) reaches an open overlay, and
      // the old local-wins rule kept the field stale until reopen. The one
      // race - a fetch that left before a local save - is closed at the
      // source: handleDescriptionUpdate invalidates in-flight requests.
      merged.description = data.description ?? null;
    }
    const currentMeta = image.value?.metadata;
    const dataMeta = data.metadata ?? {};
    if (currentMeta == null || Object.keys(currentMeta).length === 0) {
      merged.metadata = dataMeta;
    } else if (Object.keys(dataMeta).length) {
      merged.metadata = { ...currentMeta, ...dataMeta };
    }
    image.value = merged;
    // Carry the orientation into the snapshot frozen on open as well. Every
    // re-resolve of the picture reads that snapshot - the grid's list refresh
    // after a rotate, and moving to a neighbour and back - so a stale entry
    // there puts the pre-rotate `?v=` back on the <img>.
    const frozen = frozenAllImages.value;
    const frozenIndex = Array.isArray(frozen)
      ? frozen.findIndex((item) => String(item?.id) === String(imageId))
      : -1;
    if (
      frozenIndex >= 0 &&
      merged.orientation != null &&
      frozen[frozenIndex].orientation !== merged.orientation
    ) {
      const next = frozen.slice();
      next[frozenIndex] = {
        ...frozen[frozenIndex],
        orientation: merged.orientation,
      };
      frozenAllImages.value = next;
    }
    void ensureOverlayFilmstripForImage();
    void tagsPanelRef.value?.refetchPredictions(imageId);
  } catch (e) {
    console.error("Failed to fetch overlay metadata:", e);
  }
}

async function fetchFaceBboxes(imageId) {
  if (!imageId || !backendUrl.value) {
    faceBboxes.value = [];
    return;
  }
  const requestId = (faceBboxesRequestId += 1);
  const requestedImageId = imageId;
  try {
    const faces = await listPictureFaces(imageId, {
      baseUrl: backendUrl.value,
    });
    if (faceBboxesRequestId !== requestId) return;
    if (!image.value || image.value.id !== requestedImageId) return;
    const faceArray = Array.isArray(faces) ? faces : faces.faces;
    const firstFrameFaces = dedupeDetections(faceArray).filter(
      (f) =>
        f.frame_index === 0 && Array.isArray(f.bbox) && f.bbox.length === 4,
    );
    if (faceBboxesRequestId !== requestId) return;
    if (!image.value || image.value.id !== requestedImageId) return;
    faceBboxes.value = firstFrameFaces;
    // Fetch character names asynchronously to avoid delaying tag loading
    Promise.all(
      firstFrameFaces.map(async (face) => {
        if (face.character_id) {
          try {
            const data = await getCharacterName(face.character_id, {
              baseUrl: backendUrl.value,
            });
            face.character_name = data.name || null;
          } catch (e) {
            // Non-fatal: the box still renders, just without a name. Log it so
            // a systematically failing lookup is not invisible.
            console.debug(
              `Failed to resolve the name of character ${face.character_id}`,
              e,
            );
            face.character_name = null;
          }
        } else {
          face.character_name = null;
        }
      }),
    ).then(() => {
      if (faceBboxesRequestId !== requestId) return;
      if (!image.value || image.value.id !== requestedImageId) return;
      faceBboxes.value = [...firstFrameFaces];
    });
  } catch (e) {
    console.error("Error in fetchFaceBboxes:", e);
    faceBboxes.value = [];
  }
}

async function fetchDetections(imageId) {
  if (!imageId || !backendUrl.value) {
    detectionBboxes.value = [];
    return;
  }
  const requestId = (detectionBboxesRequestId += 1);
  const requestedImageId = imageId;
  try {
    const rows = await listPictureDetections(imageId, {
      baseUrl: backendUrl.value,
    });
    if (detectionBboxesRequestId !== requestId) return;
    if (!image.value || image.value.id !== requestedImageId) return;
    // The endpoint returns a bare array of detection rows.
    const detections = Array.isArray(rows) ? rows : [];
    detectionBboxes.value = dedupeDetections(detections).filter(
      (d) =>
        d.frame_index === 0 && Array.isArray(d.bbox) && d.bbox.length === 4,
    );
  } catch (e) {
    console.error("Error in fetchDetections:", e);
    if (detectionBboxesRequestId !== requestId) return;
    detectionBboxes.value = [];
  }
}

// Map each distinct detection label to a stable colour index so boxes sharing a
// label share a colour (reuses the face-bbox palette).
const detectionColorIndex = computed(() => {
  const map = new Map();
  let next = 0;
  for (const det of detectionBboxes.value) {
    const label = det?.label ?? "";
    if (!map.has(label)) map.set(label, next++);
  }
  return map;
});

function detectionBoxColor(det) {
  return faceBoxColor(detectionColorIndex.value.get(det?.label ?? "") ?? 0);
}

async function fetchCharacters() {
  if (!backendUrl.value || charactersLoading.value) return;
  charactersLoading.value = true;
  const requestEpoch = (characterThumbnailEpoch += 1);
  try {
    const data = await listCharacters({ baseUrl: backendUrl.value });
    const list = Array.isArray(data) ? data : [];
    characters.value = list;
    await Promise.all(
      list.map(async (char) => fetchCharacterThumbnail(char?.id, requestEpoch)),
    );
  } catch (e) {
    console.error("Failed to fetch characters:", e);
    characters.value = [];
  } finally {
    if (requestEpoch === characterThumbnailEpoch) {
      charactersLoading.value = false;
    }
  }
}

async function fetchCharacterThumbnail(characterId, requestEpoch) {
  if (!characterId || !backendUrl.value) return;
  try {
    const blob = await getCharacterThumbnail(characterId, {
      cacheBuster: Date.now(),
      baseUrl: backendUrl.value,
    });
    if (requestEpoch !== characterThumbnailEpoch) return;
    const blobUrl = URL.createObjectURL(blob);
    const existing = characterThumbnails.value[characterId];
    if (existing) {
      URL.revokeObjectURL(existing);
    }
    characterThumbnails.value = {
      ...characterThumbnails.value,
      [characterId]: blobUrl,
    };
  } catch (e) {
    console.error(`Failed to fetch thumbnail for character ${characterId}:`, e);
    if (requestEpoch !== characterThumbnailEpoch) return;
    characterThumbnails.value = {
      ...characterThumbnails.value,
      [characterId]: null,
    };
  }
}

function clearCharacterThumbnails() {
  Object.values(characterThumbnails.value).forEach((url) => {
    if (url) {
      URL.revokeObjectURL(url);
    }
  });
  characterThumbnails.value = {};
}

function getFaceThumbStyle(face, idx, size = FACE_THUMB_BASE) {
  const color = faceBoxColor(idx);
  const base = {
    borderColor: color,
  };
  const bbox = Array.isArray(face?.bbox) ? face.bbox : null;
  const sourceWidth = Number(
    image.value?.width || overlayDims.value.naturalWidth || 0,
  );
  const sourceHeight = Number(
    image.value?.height || overlayDims.value.naturalHeight || 0,
  );
  const sourceUrl = getFullImageUrl(image.value);
  if (!sourceUrl || !bbox || bbox.length !== 4) {
    return {
      ...base,
      width: `${size}px`,
      height: `${size}px`,
    };
  }
  const [x1, y1, x2, y2] = bbox;
  const faceW = Math.max(1, x2 - x1);
  const faceH = Math.max(1, y2 - y1);
  const imageW = sourceWidth || overlayDims.value.naturalWidth || 1;
  const imageH = sourceHeight || overlayDims.value.naturalHeight || 1;
  const maxDim = Math.max(faceW, faceH);
  const targetMax = Math.min(
    FACE_THUMB_MAX,
    Math.max(FACE_THUMB_MIN, size),
  );
  const scale = targetMax / maxDim;
  const targetW = Math.max(1, Math.round(faceW * scale));
  const targetH = Math.max(1, Math.round(faceH * scale));
  const bgWidth = Math.round(imageW * scale);
  const bgHeight = Math.round(imageH * scale);
  const bgPosX = Math.round(-x1 * scale);
  const bgPosY = Math.round(-y1 * scale);
  return {
    ...base,
    width: `${targetW}px`,
    height: `${targetH}px`,
    backgroundImage: `url(${sourceUrl})`,
    backgroundSize: `${bgWidth}px ${bgHeight}px`,
    backgroundPosition: `${bgPosX}px ${bgPosY}px`,
  };
}

async function assignFaceToCharacter(face, character) {
  if (!face?.id || !character?.id || !backendUrl.value) return;
  // Store imageId before the await in case image.value changes or is nulled during the async operation
  const capturedImageId = image.value?.id ?? null;
  try {
    await addCharacterFacesByFaceId(character.id, [face.id], {
      baseUrl: backendUrl.value,
    });
    if (Array.isArray(faceBboxes.value)) {
      faceBboxes.value = faceBboxes.value.map((entry) => {
        if (entry?.id === face.id) {
          return {
            ...entry,
            character_id: character.id,
            character_name: character.displayName || character.name || null,
          };
        }
        return entry;
      });
    }
    if (capturedImageId) {
      emit("overlay-change", {
        imageId: capturedImageId,
        fields: { faces: true },
      });
    }
  } catch (e) {
    console.error("Failed to assign character", e);
    noticeStore.error(
      `Couldn't assign that person. ${errorDetail(e) || e?.message || "Please try again."}`,
      { key: "overlay-assign-character" },
    );
  }
}

async function unassignFaceCharacter(face) {
  if (!face?.id || !face?.character_id || !backendUrl.value) return;
  // Store imageId before the await in case image.value changes or is nulled during the async operation
  const capturedImageId = image.value?.id ?? null;
  try {
    await removeCharacterFacesByFaceId(face.character_id, [face.id], {
      baseUrl: backendUrl.value,
    });
    if (Array.isArray(faceBboxes.value)) {
      faceBboxes.value = faceBboxes.value.map((entry) => {
        if (entry?.id === face.id) {
          return { ...entry, character_id: null, character_name: null };
        }
        return entry;
      });
    }
    if (capturedImageId) {
      emit("overlay-change", {
        imageId: capturedImageId,
        fields: { faces: true },
      });
    }
  } catch (e) {
    console.error("Failed to unassign character", e);
    noticeStore.error(
      `Couldn't unassign that person. ${errorDetail(e) || e?.message || "Please try again."}`,
      { key: "overlay-unassign-character" },
    );
  }
}

// The face menu performs no writes of its own, so the existing (and unchanged)
// face-level assign path stays the single place that talks to the API.
function handleFaceAssign(face, payload) {
  const character =
    sortedCharacters.value.find(
      (char) => String(char.id) === String(payload?.characterId),
    ) ??
    (payload?.characterId != null
      ? { id: payload.characterId, name: payload.characterName }
      : null);
  if (character) assignFaceToCharacter(face, character);
}

// What the trigger reads when the face already has someone: prefer the stored
// name, fall back to the character list, and finally to the id, which is what
// the select's fallback <option> used to do.
function faceAssignedName(face) {
  if (face?.character_id == null) return "";
  if (face.character_name) return face.character_name;
  const known = sortedCharacters.value.find(
    (char) => String(char.id) === String(face.character_id),
  );
  return known?.displayName || `Character ${face.character_id}`;
}

// ── Create a person from a face row (#645) ───────────────────────────────────
// The face id and the invoking select are captured at flow start so they
// survive the dialog: on save the new person is assigned to exactly that face,
// and either outcome returns focus to the select that opened the dialog.
const projectStore = useProjectStore();
const createPersonOpen = ref(false);
const createPersonCharacter = ref(null);
const createPersonProjects = ref([]);
let createPersonFaceId = null;
let createPersonFaceKey = null;

async function openCreatePersonForFace(face, query) {
  if (!face?.id || isReadOnly.value) return;
  createPersonFaceId = face.id;
  createPersonFaceKey = face.faceKey;
  const typed = typeof query === "string" ? query.trim() : "";
  const projects = await listProjects({ baseUrl: backendUrl.value }).catch(
    (e) => {
      console.warn("Couldn't list projects for the person editor", e);
      return [];
    },
  );
  createPersonProjects.value = Array.isArray(projects) ? projects : [];
  createPersonCharacter.value = {
    id: null,
    // The menu has a search box, so an unmatched query is the name the user
    // just typed; with nothing typed, the default series the sidebar and the
    // grid flow use.
    name: typed || nextFreeCharacterName(characters.value),
    description: "",
    extra_metadata: "",
    // Same project pre-fill as the grid flow, read from the store rather than
    // prop-drilled (frontend_architecture.md §4: Pinia for cross-component
    // state).
    project_id:
      projectStore.projectViewMode === "project"
        ? projectStore.selectedProjectId
        : null,
  };
  createPersonOpen.value = true;
}

/**
 * Own Escape while the person dialog is open, so it closes the dialog and
 * never the lightbox underneath.
 *
 * Verified rather than assumed. `AppDialog` calls `stopPropagation()` on its
 * own subtree, so an Escape from a focused field inside the dialog already
 * never reaches this component's window handler. But an Escape whose target is
 * OUTSIDE that subtree (focus resting on `<body>`) bubbles document → window
 * straight into `handleKeydown`, which emits "close" and drops the whole
 * lightbox behind the still-open dialog. A bubble-phase guard cannot fix that
 * one: `CharacterEditor`'s own document listener runs first and has already
 * flipped `createPersonOpen` to false by the time the window handler reads it.
 * So the key is taken in the CAPTURE phase, ahead of every bubble listener,
 * exactly as `ImageGridContextMenu` does, and the dialog is closed here.
 */
function onCreatePersonKeydownCapture(e) {
  if (!createPersonOpen.value || e.key !== "Escape") return;
  e.stopImmediatePropagation();
  e.preventDefault();
  handleCreatePersonClose();
}

function restoreCreatePersonFocus() {
  const key = createPersonFaceKey;
  createPersonFaceKey = null;
  if (key == null) return;
  nextTick(() => facesPanelRef.value?.focusRow(key));
}

function handleCreatePersonClose() {
  createPersonOpen.value = false;
  createPersonCharacter.value = null;
  createPersonFaceId = null;
  restoreCreatePersonFocus();
}

async function handleCreatePersonSaved(savedCharacter) {
  createPersonOpen.value = false;
  createPersonCharacter.value = null;
  const faceId = createPersonFaceId;
  createPersonFaceId = null;
  restoreCreatePersonFocus();
  const characterId = savedCharacter?.id;
  const name = savedCharacter?.name || "person";
  if (characterId == null || faceId == null) {
    // Defensive only: CharacterEditor unwraps `CharacterMutationResponse` and
    // withholds `saved` unless the record has an id, so this must never fire in
    // normal operation. If it does, the payload shape is wrong.
    console.error(
      "create-person: cannot assign the face. Expected the unwrapped record " +
        "{id, name, ...}; if the payload looks like {status, character} the " +
        "CharacterMutationResponse unwrap in CharacterEditor has regressed.",
      {
        payloadKeys:
          savedCharacter && typeof savedCharacter === "object"
            ? Object.keys(savedCharacter)
            : typeof savedCharacter,
        savedCharacter,
        faceId,
      },
    );
    noticeStore.error(
      `Created ${name}, but the face couldn't be assigned. Pick the person from the face menu.`,
      { key: "overlay-create-person" },
    );
    await fetchCharacters();
    emit("character-created");
    return;
  }
  const capturedImageId = image.value?.id ?? null;
  try {
    // Always the face-level path: the user pointed at one specific detection,
    // so a picture-level assignment would claim faces they did not choose.
    await addCharacterFacesByFaceId(characterId, [faceId], {
      baseUrl: backendUrl.value,
    });
    if (Array.isArray(faceBboxes.value)) {
      faceBboxes.value = faceBboxes.value.map((entry) =>
        entry?.id === faceId
          ? { ...entry, character_id: characterId, character_name: name }
          : entry,
      );
    }
    noticeStore.success(`Created ${name}, assigned to this face.`, {
      key: "overlay-create-person",
    });
    if (capturedImageId) {
      emit("overlay-change", {
        imageId: capturedImageId,
        fields: { faces: true },
      });
    }
  } catch (e) {
    console.error("Failed to assign the new person to the face", e);
    noticeStore.error(
      `Created ${name}, but couldn't assign this face. ${errorDetail(e) || e?.message || "Please try again."}`,
      { key: "overlay-create-person" },
    );
  }
  // Either way the person now exists: refresh the overlay's own list so every
  // face select offers them, and tell the grid to refresh the sidebar.
  await fetchCharacters();
  emit("character-created");
}

// Keep preload Image objects alive so the browser doesn't discard the
// in-flight requests. Replaced on every navigation.
let _preloadImages = [];

function preloadAdjacentImages() {
  const images = filmstripImages.value;
  if (!images.length || !image.value) return;
  const idx = filmstripIndexById.value.get(String(image.value.id)) ?? -1;
  if (idx === -1) return;
  const candidates = [];
  if (idx + 1 < images.length) candidates.push(images[idx + 1]);
  if (idx - 1 >= 0) candidates.push(images[idx - 1]);
  _preloadImages = candidates.flatMap((img) => {
    // Only preload still images - video files are large; let the browser
    // fetch them on demand rather than pre-requesting gigabytes of video.
    if (isSupportedVideoFile(getOverlayFormat(img))) return [];
    const url = appendShareToken(
      buildMediaUrl({ backendUrl: backendUrl.value, image: img }),
    );
    if (!url) return [];
    const probe = new Image();
    probe.src = url;
    return [probe];
  });
}

const comfyMetadata = ref(null);

const facesCollapsed = useSectionCollapse("faces");

// The panel names one picture's faces; stepping to the next shuts it, as the
// rail's does.
watch(
  () => image.value?.id,
  () => {
    namingFaces.value = false;
  },
);

watch(
  () => image.value?.id,
  (newId) => {
    if (newId) {
      overlayDims.value = {
        width: 1,
        height: 1,
        naturalWidth: 1,
        naturalHeight: 1,
        offsetX: 0,
        offsetY: 0,
      };
      videoError.value = null;
      fullImageErrorSrc.value = "";
      scheduleOverlayDimsUpdate();
      fetchFaceBboxes(newId);
      fetchDetections(newId);
      fetchOverlayMetadata(newId);
      fetchComfyWorkflow(newId);
      preloadAdjacentImages();
    } else {
      faceBboxes.value = [];
      detectionBboxes.value = [];
      videoError.value = null;
      fullImageErrorSrc.value = "";
      comfyMetadata.value = null;
      recipeTabShown.value = false;
    }
  },
  { immediate: true },
);

/**
 * Act on a realtime signal once per distinct `key`, while the overlay is open
 * on a picture, with that picture's id. `gate` says which signals count:
 * `"any"`, `"named"` (only one naming the open picture) or `"named-or-all"`
 * (also one that names no picture at all).
 */
function onPictureSignal(signal, gate, act) {
  let lastKey = 0;
  watch(signal, (payload) => {
    if (!payload || typeof payload !== "object") return;
    const nextKey = payload.key || 0;
    if (!nextKey || nextKey === lastKey) return;
    lastKey = nextKey;
    if (!open.value || !image.value?.id) return;
    if (gate !== "any") {
      const ids = Array.isArray(payload.pictureIds)
        ? payload.pictureIds.map(String)
        : [];
      const named = ids.includes(String(image.value.id));
      if (!named && !(gate === "named-or-all" && !ids.length)) return;
    }
    act(image.value.id);
  });
}

onPictureSignal(tagUpdate, "named-or-all", fetchOverlayMetadata);

onPictureSignal(descriptionUpdate, "named-or-all", fetchOverlayMetadata);

// A smart_score recompute landed somewhere (after a tag edit or a penalised-tag
// settings change). Re-fetch the open card's metadata so the panel shows the
// freshly-committed score. Deliberately NOT gated on the open card's id being in
// payload.pictureIds: unlike the tag/description watchers (whose events name the
// exact pictures they touched), a smart_score signal can legitimately omit the
// open card even when that card WAS rescored -
//   • the bulk-drain event carries a whole task batch's ids (vault.py, the
//     remaining==0 emit), which need not contain the open card;
//   • an interactive rescore that overflowed the registry is demoted onto that
//     same bulk path (smart_score_invalidation.py), losing its origin/id;
//   • Vue coalesces two signals written before the watcher flushes into the
//     latest value, dropping an intermediate one that named the open card.
// Any of those left the score stale until a full reload. Re-fetching on every
// distinct signal is safe and cheap: fetchOverlayMetadata is requestId-deduped
// and keeps the current value on a still-null read (recompute pending), so a
// redundant fetch for an unrelated batch neither flickers nor hammers.
onPictureSignal(smartScoreUpdate, "any", fetchOverlayMetadata);

// A Segment run finished. The boxes come from /pictures/{id}/detections, which
// is only read when the displayed card changes, so without this the overlay kept
// the pre-segment boxes until it was closed and reopened. Re-fetch on every
// distinct signal rather than gating on the payload's ids: two detection tasks
// completing in one Vue flush coalesce to the later payload, which can omit the
// open card. fetchDetections is requestId-deduped and drops a response for a card
// that is no longer displayed, so a redundant fetch is one cheap call, not a race.
onPictureSignal(detectionUpdate, "any", fetchDetections);

// OCR finished (or was cleared for a re-read) for some pictures. Unlike the
// detection signal this one is gated on the open picture: a text read is one
// picture's own task, and its frame names that picture.
onPictureSignal(textUpdate, "named", () => pictureText.refresh());

// The open picture was TURNED somewhere else: an undo or redo of a rotate
// (Ctrl+Z, the receipt's Undo, the toolbar), or a rotate made in another tab.
// Any of those leaves this overlay holding the pre-rotate `orientation`, which
// is the only thing `fullImageSrc` has to tell the browser the file it already
// decoded is now sideways - so the picture stayed the wrong way up until the
// lightbox was closed and reopened.
//
// It re-reads through the same `refreshAfterTurn` a rotate made HERE does, so
// the two origins cannot drift apart. The rotate made here receives its own
// echo like any other and pays one extra round of requestId-deduped reads for
// it; suppressing that would need a record of what this tab has already
// applied, and the `restored` branch in useGridRealtimeSync documents why that
// bookkeeping is the thing to avoid rather than the duplicate.
//
// Gated on the payload's ids, like the text watcher above and unlike the score
// and detection ones. Their ungating is about two signals coalescing into one
// watcher flush, which cannot happen to a socket-driven ref: `wsOrientationUpdate`
// is written at most once per `ws.onmessage`, each message is its own
// macrotask, and Vue drains the pre-flush queue on the microtask between them.
onPictureSignal(orientationUpdate, "named", (id) => {
  void refreshAfterTurn(id);
});

// The Info tab names the picture by its description's first sentence.
const pictureName = computed(() => {
  const text = String(image.value?.description || "").trim();
  if (!text) return "";
  return text.split(/(?<=[.!?])\s/)[0].replace(/[.!?]+$/, "");
});

const faceAssignItems = computed(() => {
  const faces = Array.isArray(faceBboxes.value) ? faceBboxes.value : [];
  return faces.map((face, idx) => ({
    ...face,
    faceIdx: idx,
    faceKey: face?.id ?? `face-${idx}`,
    label: `Face ${idx + 1}`,
  }));
});

const facePanelRows = computed(() =>
  faceAssignItems.value.map((face) => ({
    key: face.faceKey,
    id: face.id,
    characterId: face.character_id,
    name: faceAssignedName(face),
    label: face.label,
    hue: faceBoxColor(face.faceIdx),
    cropStyle: getFaceThumbStyle(face, face.faceIdx, 36),
    face,
  })),
);

const sortedCharacters = computed(() => {
  const list = Array.isArray(characters.value) ? characters.value : [];
  return [...list]
    .filter((char) => char && typeof char.name === "string")
    .sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    )
    .map((char) => ({
      ...char,
      displayName: char.name.charAt(0).toUpperCase() + char.name.slice(1),
    }));
});

function isValidOverlayBBox(bbox) {
  return Array.isArray(bbox) && bbox.length === 4;
}

function getOverlayBoxStyle(bbox, color) {
  if (!overlayReady.value || !isValidOverlayBBox(bbox)) {
    return { display: "none" };
  }
  const dims = overlayDims.value;
  const x1 = bbox[0];
  const y1 = bbox[1];
  const x2 = bbox[2];
  const y2 = bbox[3];
  const left = (dims.offsetX || 0) + (x1 * dims.width) / dims.naturalWidth;
  const top = (dims.offsetY || 0) + (y1 * dims.height) / dims.naturalHeight;
  const width = ((x2 - x1) * dims.width) / dims.naturalWidth;
  const height = ((y2 - y1) * dims.height) / dims.naturalHeight;
  return {
    border: `1px solid ${color}`,
    background: `${color}22`,
    left: `${left || 0}px`,
    top: `${top || 0}px`,
    width: `${width || 0}px`,
    height: `${height || 0}px`,
  };
}

/** Clicking a box selects its word (shift adds) and brings it into view. */
function onTextBoxClick(index, event) {
  pictureText.selectWord(index, Boolean(event?.shiftKey));
  descriptionPanelRef.value?.revealWord?.(index);
}

/** Return the keyboard to the overlay once a sidebar edit ends. */
function focusOverlayCanvas() {
  overlayCanvasRef.value?.focus?.();
}

function handleDescriptionUpdate(imageId, newDescription) {
  // Invalidate any in-flight metadata fetch: it left before this save and
  // would land carrying the pre-save description, which the merge below now
  // treats as authoritative.
  metadataRequestId += 1;
  if (image.value && image.value.id === imageId) {
    image.value = { ...image.value, description: newDescription };
  }
  if (Array.isArray(allImages.value)) {
    const idx = allImages.value.findIndex((img) => img && img.id === imageId);
    if (idx !== -1) {
      allImages.value[idx] = {
        ...allImages.value[idx],
        description: newDescription,
      };
    }
  }
  emit("update-description", imageId, newDescription);
}

function mediaActionInfo(target = null) {
  const media = target || image.value;
  const format = MediaFormat(media);
  if (!media?.id || !format) return null;
  const video = isSupportedVideoFile(`file.${format}`);
  return {
    ...media,
    format,
    mediaKind: video ? "video" : "picture",
    noun: video ? "video" : "picture",
    filename: safeDownloadName(
      media.original_file_name,
      `${media.id}.${format}`,
    ),
  };
}

function mediaActionError(err, fallback = "Please try again.") {
  return errorDetail(err) || err?.message || String(err || fallback);
}

function triggerMediaDownload(blob, filename) {
  const link = document.createElement("a");
  const objectUrl = URL.createObjectURL(blob);
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  window.setTimeout(() => {
    URL.revokeObjectURL(objectUrl);
    link.remove();
  }, 2000);
}

async function fetchOriginalMedia(info) {
  return downloadPicture(info.id, info.format, {
    version: info.pixel_sha,
    baseUrl: backendUrl.value,
  });
}

async function saveMedia(target = null) {
  const info = mediaActionInfo(target);
  if (!info) return false;
  try {
    const blob = await fetchOriginalMedia(info);
    triggerMediaDownload(blob, info.filename);
    noticeStore.success(`Download started for ${info.filename}.`, {
      key: "save-overlay-media",
    });
    return true;
  } catch (err) {
    console.error("Failed to save overlay media", err);
    noticeStore.error(
      `Couldn't save that ${info.noun}. ${mediaActionError(err)}`,
      { key: "save-overlay-media" },
    );
    return false;
  }
}

function savePickerOptions(info) {
  const mime = mediaMimeType(info);
  const validType =
    /^[a-z0-9.+-]+\/[a-z0-9.+-]+$/i.test(mime) &&
    /^[a-z0-9]{1,16}$/i.test(info.format);
  return {
    suggestedName: info.filename,
    ...(validType
      ? {
          types: [
            {
              description: info.mediaKind === "video" ? "Video" : "Picture",
              accept: { [mime]: [`.${info.format}`] },
            },
          ],
        }
      : {}),
  };
}

async function saveMediaAs(target = null) {
  const info = mediaActionInfo(target);
  if (!info) return false;
  const desktop = window.pixlstashDesktop;
  let pickerOpened = false;
  try {
    if (desktop?.beginMediaSaveAs && desktop?.completeMediaSaveAs) {
      const choice = await desktop.beginMediaSaveAs(info.filename);
      if (choice?.canceled) return false;
      if (!choice?.saveId)
        throw new Error(
          "The desktop save dialog did not return a save request.",
        );
      try {
        const blob = await fetchOriginalMedia(info);
        const result = await desktop.completeMediaSaveAs(
          choice.saveId,
          await blob.arrayBuffer(),
        );
        if (!result?.saved)
          throw new Error("The desktop app did not write the file.");
      } catch (err) {
        await desktop.cancelMediaSaveAs?.(choice.saveId);
        throw err;
      }
      noticeStore.success(`Saved ${info.filename}.`, {
        key: "save-overlay-media-as",
      });
      return true;
    }

    if (typeof window.showSaveFilePicker === "function") {
      // The picker must be opened before the media fetch: the web API requires
      // transient user activation, while the later writable stream does not.
      const handle = await window.showSaveFilePicker(savePickerOptions(info));
      pickerOpened = true;
      const savedFilename = safeDownloadName(handle?.name, info.filename);
      const blob = await fetchOriginalMedia(info);
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      noticeStore.success(`Saved ${savedFilename}.`, {
        key: "save-overlay-media-as",
      });
      return true;
    }

    const fallbackFilename = await requestFallbackSaveFilename(info);
    if (!fallbackFilename) return false;
    // A direct backend URL can override the anchor's download name through its
    // response headers (observed in Firefox). Reuse regular Save's authenticated
    // blob path so the filename chosen in our dialog remains authoritative.
    const blob = await fetchOriginalMedia(info);
    triggerMediaDownload(blob, fallbackFilename);
    noticeStore.info(
      `Download started as ${fallbackFilename}. Your browser controls the download folder.`,
      { key: "save-overlay-media-as" },
    );
    return true;
  } catch (err) {
    // Dismissing the picker is not an error. Once a handle exists, an AbortError
    // can instead mean the write was interrupted and should be reported.
    if (!pickerOpened && err?.name === "AbortError") return false;
    console.error("Failed to save overlay media as", err);
    noticeStore.error(
      `Couldn't save that ${info.noun}. ${mediaActionError(err)}`,
      { key: "save-overlay-media-as" },
    );
    return false;
  }
}

function copyAvailability() {
  const desktopCapable =
    typeof window.pixlstashDesktop?.copyPngToClipboard === "function";
  const browserCapable = Boolean(
    navigator?.clipboard?.write && typeof window.ClipboardItem === "function",
  );
  if (!desktopCapable && !browserCapable) {
    return {
      available: false,
      reason: "This browser cannot copy image pixels. Save the media instead.",
    };
  }
  const video = isSupportedVideoFile(getOverlayFormat(image.value));
  if (video) {
    const el = videoRef.value;
    if (!el || el.readyState < 2 || !el.videoWidth || !el.videoHeight) {
      return {
        available: false,
        reason: "The video frame is still loading and cannot be copied yet.",
      };
    }
  } else {
    const el = imgRef.value;
    if (!el?.complete || !el.naturalWidth || !el.naturalHeight) {
      return {
        available: false,
        reason: "The picture is still loading and cannot be copied yet.",
      };
    }
  }
  return { available: true, reason: "" };
}

async function renderMediaPng(target = null) {
  const info = mediaActionInfo(target);
  if (!info || String(info.id) !== String(image.value?.id)) {
    throw new Error("That media is no longer displayed.");
  }
  const source = info.mediaKind === "video" ? videoRef.value : imgRef.value;
  const width =
    info.mediaKind === "video" ? source?.videoWidth : source?.naturalWidth;
  const height =
    info.mediaKind === "video" ? source?.videoHeight : source?.naturalHeight;
  if (!source || !width || !height) {
    throw new Error(
      info.mediaKind === "video"
        ? "The current video frame is not ready."
        : "The picture is not ready.",
    );
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("This browser cannot create a PNG image.");
  try {
    ctx.drawImage(source, 0, 0, width, height);
  } catch (err) {
    throw new Error(
      `The media pixels could not be read. ${mediaActionError(err)}`,
      { cause: err },
    );
  }
  const blob = await canvasToBlob(canvas, "image/png");
  if (!blob)
    throw new Error("This browser could not encode the pixels as PNG.");
  return blob;
}

async function copyMedia(target = null) {
  const info = mediaActionInfo(target);
  if (!info) return false;
  const availability = copyAvailability();
  if (!availability.available) {
    noticeStore.error(availability.reason, { key: "copy-overlay-media" });
    return false;
  }
  try {
    const pngPromise = renderMediaPng(info);
    if (window.pixlstashDesktop?.copyPngToClipboard) {
      const png = await pngPromise;
      const result = await window.pixlstashDesktop.copyPngToClipboard(
        await png.arrayBuffer(),
      );
      if (!result?.copied)
        throw new Error("The desktop clipboard rejected the image.");
    } else {
      // Pass the pending PNG promise to ClipboardItem and call write immediately;
      // this preserves the transient user activation required by some browsers.
      const item = new ClipboardItem({ "image/png": pngPromise });
      await navigator.clipboard.write([item]);
    }
    noticeStore.success(
      info.mediaKind === "video"
        ? "Copied the current frame as PNG."
        : "Copied the picture as PNG.",
      { key: "copy-overlay-media" },
    );
    return true;
  } catch (err) {
    console.warn("Failed to copy overlay media pixels", err);
    noticeStore.error(
      `Couldn't copy the ${info.mediaKind === "video" ? "current frame" : "picture"}. ${mediaActionError(err, "Check clipboard permission and try again.")}`,
      { key: "copy-overlay-media" },
    );
    return false;
  }
}

function canvasToBlob(canvas, type) {
  return new Promise((resolve) => {
    if (!canvas?.toBlob) {
      resolve(null);
      return;
    }
    canvas.toBlob(
      (blob) => {
        resolve(blob || null);
      },
      type,
      0.95,
    );
  });
}

defineExpose({
  // The picture on screen, which the grid selects when the reader closes.
  currentPicture: () => image.value,
  saveMedia,
  saveMediaAs,
  copyMedia,
  showRunResult,
  moveStackMember,
  // A Segment run started: press the toggle so its boxes show when they land.
  showSegmentation: () => {
    showDetections.value = true;
  },
});
</script>
<style scoped src="./ImageOverlay.css"></style>
