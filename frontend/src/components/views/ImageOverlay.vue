<template>
  <div v-if="open" class="image-overlay" @click.self="handleBackdropClick">
    <div
      class="overlay-shell"
      :class="{ 'chrome-hidden': chromeHidden, 'sidebar-open': sidebarOpen }"
      @mousemove="handleMouseActivity"
      @mousedown="handleMouseActivity"
      @click="handleOverlayClick"
      @wheel.passive="handleWheelActivity"
    >
      <header
        ref="topbarRef"
        class="overlay-topbar"
        :class="{ hidden: chromeHidden }"
      >
        <button
          class="overlay-close"
          @click="emit('close')"
          aria-label="Close (ESC)"
          title="Close (ESC)"
        >
          <v-icon size="18">mdi-close</v-icon>
          <span>Close</span>
        </button>
        <div class="overlay-title">
          <button
            class="overlay-desc-teaser"
            type="button"
            :disabled="!image"
            @click="openSidebarFromTeaser"
          >
            {{ descriptionTeaser || "Add a description" }}
          </button>
        </div>
        <div
          class="overlay-character-names"
          v-for="(face, idx) in faceBboxes"
          :key="idx"
        >
          <span
            v-if="face.character_name"
            :style="{ color: faceBoxColor(idx) }"
          >
            {{ face.character_name || "Unknown" }}
          </span>
        </div>
        <div class="overlay-top-actions">
          <v-tooltip
            v-if="image && isCurrentLocked"
            location="bottom"
            :text="currentLockReason"
          >
            <template #activator="{ props: lockTipProps }">
              <div
                v-bind="lockTipProps"
                class="overlay-lock-chip"
                :class="{ hidden: chromeHidden }"
                role="img"
                :aria-label="currentLockReason"
              >
                <v-icon size="16">mdi-lock-outline</v-icon>
                <span class="overlay-lock-chip-text">Locked</span>
              </div>
            </template>
          </v-tooltip>
          <v-menu
            v-if="!isReadOnly"
            v-model="pluginMenuOpen"
            :close-on-content-click="false"
            location-strategy="connected"
            location="bottom end"
            origin="top end"
            transition="scale-transition"
          >
            <template #activator="{ props }">
              <button
                v-bind="props"
                class="overlay-icon-btn overlay-comfy-activator"
                type="button"
                title="Filters"
                aria-label="Filters"
                :class="{
                  hidden: chromeHidden,
                  'overlay-icon-btn--active': pluginMenuOpen,
                }"
              >
                <v-icon size="20">mdi-tune-variant</v-icon>
                <span class="overlay-comfy-activator-label">Filters</span>
              </button>
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
          <div v-if="comfyuiConfigured" class="overlay-menu-anchor">
            <button
              class="overlay-icon-btn overlay-comfy-activator"
              type="button"
              title="Edit with ComfyUI"
              aria-label="Edit with ComfyUI"
              :class="{
                hidden: chromeHidden,
                'overlay-icon-btn--active': comfyuiMenuOpen,
              }"
            >
              <v-icon size="20">mdi-robot</v-icon>
              <span class="overlay-comfy-activator-label">I2I</span>
            </button>
            <v-menu
              v-model="comfyuiMenuOpen"
              activator="parent"
              :close-on-content-click="false"
              location-strategy="connected"
              location="bottom end"
              origin="top end"
              transition="scale-transition"
            >
              <div class="overlay-comfy-panel">
                <div class="overlay-comfy-header">Edit with ComfyUI</div>
                <div v-if="comfyuiWorkflowLoading" class="overlay-comfy-status">
                  Loading workflows...
                </div>
                <div v-else class="overlay-comfy-body">
                  <div v-if="comfyuiWorkflowError" class="overlay-comfy-error">
                    {{ comfyuiWorkflowError }}
                  </div>
                  <div
                    v-if="!validComfyWorkflows.length"
                    class="overlay-comfy-warning"
                  >
                    No valid workflows found. Workflows need a
                    {{ imagePlaceholderLabel }} placeholder.
                  </div>
                  <label class="overlay-comfy-field-label">Workflow</label>
                  <select
                    v-model="comfyuiSelectedWorkflow"
                    class="overlay-comfy-select"
                    :disabled="!validComfyWorkflows.length"
                  >
                    <option
                      v-for="workflow in validComfyWorkflows"
                      :key="workflow.name"
                      :value="workflow.name"
                    >
                      {{ workflow.display_name || workflow.name }}
                    </option>
                  </select>
                  <div
                    v-if="invalidComfyWorkflows.length"
                    class="overlay-comfy-note"
                  >
                    {{ invalidComfyWorkflows.length }} workflow(s) missing
                    required placeholders.
                  </div>
                  <template v-if="showComfyuiCaptionInput">
                    <label class="overlay-comfy-field-label">Caption</label>
                    <div class="overlay-comfy-textarea-wrap">
                      <div
                        v-if="showComfyuiCaptionHelp"
                        class="overlay-comfy-help"
                      >
                        Add edit caption here
                      </div>
                      <textarea
                        v-model="comfyuiCaption"
                        class="overlay-comfy-textarea"
                        rows="6"
                        @input="comfyuiCaptionTouched = true"
                        @focus="comfyuiCaptionFocused = true"
                        @blur="comfyuiCaptionFocused = false"
                      ></textarea>
                    </div>
                  </template>
                  <label class="overlay-comfy-checkbox-row">
                    <input v-model="stackI2IOutputs" type="checkbox" />
                    <span>Stack new images with the originals</span>
                  </label>
                  <div class="overlay-comfy-actions">
                    <button
                      class="overlay-comfy-run"
                      type="button"
                      :disabled="!canRunComfyWorkflow"
                      @click.stop="runComfyWorkflow"
                    >
                      <v-icon
                        size="16"
                        :class="{ 'mdi-spin': comfyuiRunLoading }"
                      >
                        {{ comfyuiRunLoading ? "mdi-loading" : "mdi-play" }}
                      </v-icon>
                      <span>{{ comfyuiRunLoading ? "Running" : "Run" }}</span>
                    </button>
                  </div>
                  <div v-if="comfyuiRunError" class="overlay-comfy-error">
                    {{ comfyuiRunError }}
                  </div>
                  <div v-if="comfyuiRunSuccess" class="overlay-comfy-success">
                    {{ comfyuiRunSuccess }}
                  </div>
                </div>
              </div>
            </v-menu>
          </div>
          <AddToEntityControl
            v-if="image && !isReadOnly"
            type="set"
            ref="addToSetControlRef"
            :key="addToSetControlKey"
            :subject-ids="[image.id]"
            :include-deleted-members="true"
            :force-dark="true"
            :disabled="!!stackGroupingLockReason"
            :title="stackGroupingLockReason || undefined"
            :locked-set-ids="lockedSetsStore.lockedSetIds"
            :class="{ hidden: chromeHidden }"
            @added="(payload) => emit('added-to-set', payload)"
          />
          <AddToEntityControl
            v-if="image && !isReadOnly"
            type="project"
            :subject-ids="[image.id]"
            :include-deleted-members="true"
            :expand-stacks="false"
            :force-dark="true"
            :disabled="!!stackGroupingLockReason"
            :title="stackGroupingLockReason || undefined"
            :class="{ hidden: chromeHidden }"
            @selected="(payload) => emit('set-project', payload)"
          />
          <StarRatingOverlay
            v-if="image && !isMobile"
            :class="{ hidden: chromeHidden }"
            :score="isReadOnly ? guestScore || 0 : image?.score || 0"
            :readonly="!isReadOnly && isCurrentLocked"
            icon-size="large"
            @set-score="setScore"
          />
          <v-menu
            v-if="image && isMobile"
            v-model="starMenuOpen"
            location="bottom end"
            origin="top end"
            transition="scale-transition"
          >
            <template #activator="{ props: menuProps }">
              <button
                v-bind="menuProps"
                class="overlay-icon-btn overlay-star-mobile-btn"
                type="button"
                title="Set rating (1–5)"
                aria-label="Set rating"
                :class="{ hidden: chromeHidden }"
              >
                <v-icon size="18" color="rgba(var(--v-theme-accent))"
                  >mdi-star</v-icon
                >
                <span class="overlay-star-mobile-label">{{
                  isReadOnly ? guestScore || 0 : image?.score || 0
                }}</span>
              </button>
            </template>
            <div class="overlay-star-menu">
              <button
                v-for="n in [0, 1, 2, 3, 4, 5]"
                :key="n"
                class="overlay-star-menu-item"
                :class="{
                  'overlay-star-menu-item--active':
                    (isReadOnly ? guestScore || 0 : image?.score || 0) === n,
                }"
                type="button"
                @click.stop="setScore(n)"
              >
                <span class="overlay-star-menu-stars">
                  <v-icon
                    v-for="s in 5"
                    :key="s"
                    size="16"
                    :color="
                      s <= n
                        ? 'rgba(var(--v-theme-accent))'
                        : 'rgba(255,255,255,0.2)'
                    "
                    >mdi-star</v-icon
                  >
                </span>
                <span class="overlay-star-menu-label">{{
                  n === 0 ? "No rating" : n
                }}</span>
              </button>
            </div>
          </v-menu>
          <button
            class="overlay-icon-btn"
            type="button"
            title="Toggle face bounding boxes"
            aria-label="Toggle face bounding boxes"
            @click.stop="toggleFaceBbox"
            :class="{
              hidden: chromeHidden,
              'overlay-icon-btn--active': showFaceBbox,
            }"
          >
            <v-icon size="20">mdi-face-recognition</v-icon>
          </button>
          <button
            class="overlay-icon-btn"
            type="button"
            title="Toggle object detection boxes"
            aria-label="Toggle object detection boxes"
            @click.stop="toggleDetections"
            :class="{
              hidden: chromeHidden,
              'overlay-icon-btn--active': showDetections,
            }"
          >
            <v-icon size="20">mdi-shape-outline</v-icon>
          </button>
          <button
            v-if="!isMobile && !isReadOnly"
            class="overlay-icon-btn"
            type="button"
            title="Draw face bounding box"
            aria-label="Draw face bounding box"
            @click.stop="beginDrawMode('face')"
            :class="{
              hidden: chromeHidden,
              'overlay-icon-btn--active': drawMode === 'face',
            }"
          >
            <v-icon size="20">mdi-account-plus</v-icon>
          </button>

          <!-- Rotate, in place and immediately: one click is one 90° step, no
               dialog, no direction picker and no confirmation. The safety net
               is undo (the receipt below narrates every step and offers it),
               which is the right net for an action that is instant, lossless
               and reversible - a confirm on every quarter-turn would cost more
               than the mistake it prevents.

               Greyed rather than hidden when the file cannot carry a rotation
               every renderer agrees on: the tooltip is what teaches that
               Filters > Rotate still makes a rotated copy, and a hidden control
               teaches nothing. -->
          <template v-if="image && !isReadOnly">
            <v-tooltip
              location="bottom"
              :text="rotateLeftTitle"
              :disabled="!rotateDisabledReason"
            >
              <template #activator="{ props: rotateLeftTipProps }">
                <button
                  v-bind="rotateLeftTipProps"
                  class="overlay-icon-btn"
                  type="button"
                  :title="rotateLeftTitle"
                  :aria-label="rotateLeftTitle"
                  aria-keyshortcuts="["
                  :disabled="!canRotateCurrent"
                  @click.stop="rotateCurrentImage(ROTATE_CCW)"
                  :class="{ hidden: chromeHidden }"
                >
                  <v-icon size="20">mdi-rotate-left</v-icon>
                </button>
              </template>
            </v-tooltip>
            <v-tooltip
              location="bottom"
              :text="rotateRightTitle"
              :disabled="!rotateDisabledReason"
            >
              <template #activator="{ props: rotateRightTipProps }">
                <button
                  v-bind="rotateRightTipProps"
                  class="overlay-icon-btn"
                  type="button"
                  :title="rotateRightTitle"
                  :aria-label="rotateRightTitle"
                  aria-keyshortcuts="]"
                  :disabled="!canRotateCurrent"
                  @click.stop="rotateCurrentImage(ROTATE_CW)"
                  :class="{ hidden: chromeHidden }"
                >
                  <v-icon size="20">mdi-rotate-right</v-icon>
                </button>
              </template>
            </v-tooltip>
          </template>

          <!-- The zoom readout lives ON the control (owner ruling): a live
               whole-percent of natural size beside the retained icon. The
               label width is reserved once (5ch, tabular numerals) so the
               toolbar never jumps as the value changes. Click semantics match
               Z: at fit → snap to 100%, otherwise → snap to fit. -->
          <button
            class="overlay-icon-btn zoom-btn"
            type="button"
            :title="zoomButtonTitle"
            :aria-label="zoomButtonTitle"
            @click="toggleZoomSnap"
          >
            <v-icon>mdi-magnify</v-icon>
            <span class="zoom-btn-label">{{ zoomButtonLabel }}</span>
          </button>
          <button
            class="overlay-icon-btn overlay-topbar-sidebar-toggle"
            type="button"
            title="Toggle sidebar (S)"
            aria-label="Toggle sidebar (S)"
            @click="toggleSidebar"
          >
            <v-icon>{{
              sidebarOpen ? "mdi-arrow-collapse-right" : "mdi-arrow-expand-left"
            }}</v-icon>
          </button>
        </div>
      </header>

      <div
        v-if="comfyuiProgress && comfyuiProgress.visible"
        class="overlay-progress overlay-progress--comfyui"
        :class="{
          'overlay-progress--error': comfyuiProgress.status === 'failed',
        }"
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
        ref="overlayMainRef"
        class="overlay-main"
        :style="filmstripStyleVars"
      >
        <div
          ref="overlayCanvasRef"
          class="overlay-canvas"
          tabindex="0"
          aria-label="Image - right-click or press the menu key for actions"
          @touchstart="onTouchStart"
          @touchmove="onTouchMove"
          @touchend="onTouchEnd"
          @dblclick="onCanvasDblClick"
          @wheel.prevent="onWheelZoom"
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
              <template v-if="showFaceBbox && overlayReady">
                <div v-if="faceBboxes.length === 0" class="face-bbox-empty">
                  No bboxes found
                </div>
                <div
                  v-for="(face, idx) in faceBboxes"
                  :key="`face-${idx}`"
                  class="face-bbox-overlay"
                  :style="getOverlayBoxStyle(face.bbox, faceBoxColor(idx))"
                >
                  <span class="face-bbox-label">
                    {{ face.character_name || `Face ${idx + 1}` }}
                  </span>
                </div>
              </template>
              <template v-if="showDetections && overlayReady">
                <div
                  v-if="detectionBboxes.length === 0"
                  class="face-bbox-empty"
                >
                  No detections found
                </div>
                <div
                  v-for="(det, idx) in detectionBboxes"
                  :key="`det-${idx}`"
                  class="face-bbox-overlay"
                  :style="getOverlayBoxStyle(det.bbox, detectionBoxColor(det))"
                >
                  <span class="face-bbox-label">
                    {{ det.label || `Object ${idx + 1}` }}
                  </span>
                </div>
              </template>
              <!-- The rubber-band rectangle rides INSIDE the transformed
                   media (like the face boxes), so the same layout-space math
                   is correct at every continuous zoom scale; the draw layer
                   below only owns the pointer events and the hint. -->
              <div
                v-if="drawMode && drawRectStyle"
                class="overlay-draw-rect"
                :style="drawRectStyle"
              ></div>
            </div>
          </div>

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
            title="Previous (←)"
          >
            <v-icon>mdi-chevron-left</v-icon>
          </button>
          <button
            class="overlay-nav overlay-nav-right"
            :class="{ hidden: chromeHidden }"
            @click.stop="showNextImage"
            @dblclick.stop
            aria-label="Next (→)"
            title="Next (→)"
          >
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

        <OverlayFilmstrip
          ref="filmstripRef"
          :items="filmstripCanvasWindow"
          :canvas-style="filmstripCanvasStyle"
          :hidden="chromeHidden"
          @select="selectImageByIndex"
          @toggle-expand="toggleFilmstripStackExpand"
          @prefetch="prefetchFilmstripStackMembers"
          @navigate="onFilmstripNavigate"
        />

        <aside
          class="overlay-sidebar"
          :class="{ open: sidebarOpen, hidden: chromeHidden }"
        >
          <OverlayDescriptionPanel
            ref="descriptionPanelRef"
            :image="image"
            :locked="isCurrentLocked"
            :lock-note="currentLockReason"
            @update-description="handleDescriptionUpdate"
            @editing-finished="focusOverlayCanvas"
          />

          <div class="sidebar-section sidebar-section--faces">
            <div
              class="section-header section-header--collapsible"
              @click="facesCollapsed = !facesCollapsed"
            >
              <span>Faces</span>
              <v-icon size="16" style="opacity: 0.6">{{
                facesCollapsed ? "mdi-chevron-right" : "mdi-chevron-down"
              }}</v-icon>
            </div>
            <template v-if="!facesCollapsed">
              <div v-if="faceAssignItems.length" class="face-assign-grid">
                <div
                  v-for="face in faceAssignItems"
                  :key="face.faceKey"
                  class="face-assign-card"
                >
                  <div class="face-assign-row">
                    <div class="face-assign-thumb">
                      <div
                        class="face-assign-crop"
                        :style="getFaceThumbStyle(face, face.faceIdx)"
                      ></div>
                    </div>
                    <div class="face-assign-meta">
                      <div
                        class="face-assign-label"
                        :style="{ color: faceBoxColor(face.faceIdx) }"
                      >
                        {{ face.label }}
                      </div>
                      <!-- A native <select> cannot carry the create row's
                           highlight: macOS Chrome and Safari draw select
                           popups as OS menus that ignore option colour. This
                           is the same menu language as the rest of the app
                           (AddToEntityControl's force-dark skin), in its
                           single-select face mode. Face mode picks one person
                           for one face, so it has no subject list to compute a
                           tri-state across and passes an empty one. -->
                      <div class="face-assign-person">
                        <AddToEntityControl
                          :ref="(el) => setFaceMenuRef(face.faceKey, el)"
                          type="face"
                          allow-create
                          float-menu
                          :subject-ids="[]"
                          :force-dark="true"
                          :face-id="face.id"
                          :assigned-character-id="face.character_id"
                          :assigned-character-name="faceAssignedName(face)"
                          :readonly="isReadOnly"
                          :disabled="!face.id || isReadOnly"
                          @assign="handleFaceAssign(face, $event)"
                          @unassign="unassignFaceCharacter(face)"
                          @create="openCreatePersonForFace(face, $event)"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div v-else class="face-assign-empty">No faces detected</div>
            </template>
          </div>

          <OverlayTagsPanel
            ref="tagsPanelRef"
            :image="image"
            :hidden-tags="hiddenTags"
            :apply-tag-filter="applyTagFilter"
            :locked="isCurrentLocked"
            :lock-note="currentLockReason"
            @update-tags="handleTagsUpdate"
            @overlay-change="(payload) => emit('overlay-change', payload)"
            @add-tag="(imageId, tag) => emit('add-tag', imageId, tag)"
            @request-metadata-refresh="fetchOverlayMetadata"
          />

          <OverlayMetadataPanel
            :image="image"
            :comfy-metadata="comfyMetadata"
            :date-format="dateFormat"
            :video-duration="videoMeta.duration"
          />
        </aside>

        <!-- The lightbox's own narration of an undoable action. Last child of
             `.overlay-main` on purpose: this is where `--filmstrip-rail-width`
             is declared, last-in-DOM is last-in-tab-order, and it escapes
             `.overlay-canvas`'s `overflow: hidden`, which would clip the focus
             ring on the Undo button. -->
        <OverlayActionReceipt
          v-if="!isReadOnly"
          ref="receiptRef"
          :chrome-hidden="chromeHidden"
        />
      </div>
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
</template>

<script setup>
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
  isSupportedVideoFile,
  getOverlayFormat,
  buildMediaUrl,
  MediaFormat,
  mediaMimeType,
  safeDownloadName,
  setInternalDragPayload,
} from "../../utils/media.js";
import { API_BASE_URL, appendShareToken, isReadOnly } from "../../utils/apiClient";
import {
  getPictureMetadata,
  listPictureFaces,
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
import { listStackPictures } from "../../api/stacks";
import {
  listWorkflows,
  runImageToImage,
  getPictureWorkflow,
} from "../../api/comfyui";
import { listProjects } from "../../api/projects";
import { useGenStackPrefsStore } from "../../stores/useGenStackPrefsStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useOperationStore } from "../../stores/useOperationStore";
import { useProjectStore } from "../../stores/useProjectStore";
import { nextFreeCharacterName } from "../../utils/characterCreateFlow.js";
import AddToEntityControl from "../widgets/AddToEntityControl.vue";
import CharacterEditor from "../editors/CharacterEditor.vue";
import OverlayDescriptionPanel from "./OverlayDescriptionPanel.vue";
import OverlayFilmstrip from "./OverlayFilmstrip.vue";
import OverlayMetadataPanel from "./OverlayMetadataPanel.vue";
import OverlayTagsPanel from "./OverlayTagsPanel.vue";
import OverlayActionReceipt from "../widgets/OverlayActionReceipt.vue";
import OverlaySaveAsDialog from "../widgets/OverlaySaveAsDialog.vue";
import PluginParametersUI from "../widgets/PluginParametersUI.vue";
import StarRatingOverlay from "../widgets/StarRatingOverlay.vue";
import {
  applyStackBackgroundAlpha,
  faceBoxColor,
  getStackColor,
  getStackColorIndexFromId,
  toggleScore,
} from "../../utils/utils.js";
import { isEditableElement, isTypingTarget } from "../../utils/dom.js";
import {
  getPictureStackId,
  getStackPositionValue,
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
  hiddenTags: { type: Array, default: () => [] },
  applyTagFilter: { type: Boolean, default: false },
  dateFormat: { type: String, default: "locale" },
  showStacks: { type: Boolean, default: true },
  showProblemIcon: { type: Boolean, default: true },
  availablePlugins: { type: Array, default: () => [] },
  comfyuiProgress: { type: Object, default: null },
  comfyuiProgressPercent: { type: Number, default: 0 },
  pluginProgress: { type: Object, default: null },
  pluginProgressPercent: { type: Number, default: 0 },
  comfyuiClientId: { type: String, default: "" },
  comfyuiConfigured: { type: Boolean, default: false },
  guestScore: { type: Number, default: 0 },
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
  hiddenTags,
  applyTagFilter,
  showStacks,
  showProblemIcon,
  availablePlugins,
  comfyuiProgress,
  comfyuiProgressPercent,
  pluginProgress,
  pluginProgressPercent,
  comfyuiClientId,
  comfyuiConfigured,
  guestScore,
} = toRefs(props);

const image = ref(null);
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
const sidebarOpen = ref(true);
const chromeHidden = ref(false);
const chromeRevealTimestamp = ref(0);
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
  const target = targetFromAll
    ? targetFromAll
    : filmstripImageById.value.get(nextIdKey);
  if (target) {
    const existingTags = getTagList(image.value?.tags);
    const targetTags = getTagList(target.tags);
    const existingDescription = image.value?.description;
    const existingSmartScore = image.value?.smartScore;
    const existingScore = image.value?.score;
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

const emit = defineEmits([
  "close",
  "apply-score",
  "set-guest-score",
  "add-tag",
  "update-description",
  "overlay-change",
  "added-to-set",
  "set-project",
  "comfyui-run",
  "run-plugin",
  "request-context-menu",
  "character-created",
]);

const descriptionPanelRef = ref(null);
const isDescriptionEditing = computed(
  () => descriptionPanelRef.value?.isEditingDescription ?? false,
);
const imagePlaceholderLabel = "{{image_path}}";
const captionPlaceholderLabel = "{{caption}}";
const descriptionTeaser = computed(() => {
  const desc = image.value?.description || "";
  const trimmed = desc.trim();
  if (!trimmed) return "";
  const match = trimmed.match(/[^.!?]+[.!?]?/);
  return match ? match[0].trim() : trimmed;
});

const lastTagUpdateKey = ref(0);
const lastDescriptionUpdateKey = ref(0);
const lastSmartScoreUpdateKey = ref(0);
const lastDetectionUpdateKey = ref(0);
const addToSetControlKey = ref(0);
const comfyuiMenuOpen = ref(false);
const pluginMenuOpen = ref(false);
const starMenuOpen = ref(false);
let menuWasOpenOnPointerDown = false;
const comfyuiWorkflows = ref([]);
const comfyuiWorkflowLoading = ref(false);
const comfyuiWorkflowError = ref("");
const comfyuiSelectedWorkflow = ref("");
const comfyuiCaption = ref("");
const comfyuiCaptionTouched = ref(false);
const comfyuiCaptionFocused = ref(false);
const comfyuiRunLoading = ref(false);
const comfyuiRunError = ref("");
const comfyuiRunSuccess = ref("");
const overlaySelectedPluginName = ref("");
const overlayPluginParameters = ref({});

// Lock state for the currently displayed picture: drives the toolbar chip, the
// score/tags/description gating, and the panel lock notes.
const lockedSetsStore = useLockedSetsStore();
const isCurrentLocked = computed(() =>
  lockedSetsStore.isLocked(image.value?.id),
);
const currentLockReason = computed(() =>
  lockedSetsStore.lockReason(image.value?.id),
);

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
const rotateMetadataInFlightImageIds = new Set();

/** Why the rotate controls are greyed, or `null` when they are live. */
const rotateDisabledReason = computed(() =>
  image.value ? rotateBlockReason([image.value]) : null,
);
const canRotateCurrent = computed(
  () => !isReadOnly.value && !isCurrentLocked.value && canRotateInPlace(image.value),
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
 * One 90° step on one picture, plus the three refreshes it owes.
 *
 * They travel three different paths, which is why this is not a one-liner:
 *
 *   * the **operation log**, so the receipt narrates the step and offers undo.
 *     Anything the server refused rides that same pill as a second sentence -
 *     a separate notice would be the half the user dismisses;
 *   * the **overlay's own record**, so `orientation` moves and `mediaVersion`
 *     rebuilds the `<img>`'s cache-buster. A rotate leaves the pixels exactly
 *     where they were, so the orientation is the ONLY thing that can tell the
 *     browser the file it decoded is now sideways;
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
    const imageIdKey = String(imageId);
    rotateMetadataInFlightImageIds.add(imageIdKey);
    try {
      await fetchOverlayMetadata(imageId);
    } finally {
      rotateMetadataInFlightImageIds.delete(imageIdKey);
    }
    // The boxes are drawn in the file's own coordinate space, which the turn
    // just redefined. Re-read rather than transform them here: whatever the
    // server now reports is what the grid and every other surface will draw.
    fetchFaceBboxes(imageId);
    fetchDetections(imageId);
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
const stackI2IOutputs = computed({
  get: () => genStackPrefs.stackI2IOutputs,
  set: (val) => genStackPrefs.setStackI2IOutputs(val),
});
const stackFilterOutputs = computed({
  get: () => genStackPrefs.stackFilterOutputs,
  set: (val) => genStackPrefs.setStackFilterOutputs(val),
});

// Auto-close timer for the I2I menu after a successful queue.
let comfyuiCloseTimer = null;
function clearComfyuiCloseTimer() {
  if (comfyuiCloseTimer !== null) {
    clearTimeout(comfyuiCloseTimer);
    comfyuiCloseTimer = null;
  }
}
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

const COMFYUI_PROMPT_STORAGE_PREFIX = "pixlstash:comfyuiPrompt:";

function getComfyuiPromptStorageKey() {
  if (typeof window === "undefined") return "";
  const workflow = String(comfyuiSelectedWorkflow.value || "default");
  return `${COMFYUI_PROMPT_STORAGE_PREFIX}${workflow}`;
}

function loadComfyuiPromptFromSession() {
  if (typeof window === "undefined") return null;
  if (!showComfyuiCaptionInput.value) return null;
  const key = getComfyuiPromptStorageKey();
  if (!key) return null;
  return window.sessionStorage?.getItem(key);
}

function persistComfyuiPromptToSession() {
  if (typeof window === "undefined") return;
  if (!showComfyuiCaptionInput.value) return;
  const key = getComfyuiPromptStorageKey();
  if (!key) return;
  const value = comfyuiCaption.value || "";
  window.sessionStorage?.setItem(key, value);
}

const validComfyWorkflows = computed(() =>
  (comfyuiWorkflows.value || []).filter(
    (workflow) => workflow?.valid && workflow?.workflow_type === "i2i",
  ),
);
const invalidComfyWorkflows = computed(() =>
  (comfyuiWorkflows.value || []).filter(
    (workflow) => !workflow?.valid && workflow?.workflow_type === "i2i",
  ),
);
const selectedComfyWorkflow = computed(() =>
  (comfyuiWorkflows.value || []).find(
    (workflow) => workflow?.name === comfyuiSelectedWorkflow.value,
  ),
);
const selectedComfyUsesCaption = computed(() => {
  const missing = Array.isArray(
    selectedComfyWorkflow.value?.missing_placeholders,
  )
    ? selectedComfyWorkflow.value.missing_placeholders
    : [];
  return !missing.includes(captionPlaceholderLabel);
});
const showComfyuiCaptionInput = computed(() => selectedComfyUsesCaption.value);
const canRunComfyWorkflow = computed(() => {
  return (
    !!image.value?.id &&
    !!comfyuiSelectedWorkflow.value &&
    !comfyuiRunLoading.value
  );
});
const showComfyuiCaptionHelp = computed(() => {
  return (
    showComfyuiCaptionInput.value &&
    !comfyuiCaptionFocused.value &&
    !comfyuiCaption.value
  );
});

watch(open, (value) => {
  if (!value) {
    resetOverlayStackState();
    // Release the frozen navigation backbone so the next open re-snapshots the
    // current grid, and so closed-overlay reads fall through to live allImages.
    frozenAllImages.value = null;
    pluginMenuOpen.value = false;
    comfyuiMenuOpen.value = false;
    chromeHidden.value = false;
    chromeRevealTimestamp.value = 0;
    addToSetControlKey.value += 1;
    zoom.reset();
    resetComfyState();
  } else {
    // Snapshot the grid sequence up front so prev/next stay stable for the
    // whole lifetime of the overlay regardless of edits or background refetches.
    captureFrozenAllImages();
    resetOverlayStackState();
    applyInitialExpandedStackState();
    pluginMenuOpen.value = false;
    comfyuiMenuOpen.value = false;
    chromeRevealTimestamp.value = Date.now();
    const stored = loadComfyuiPromptFromSession();
    if (stored != null) {
      comfyuiCaption.value = stored;
      comfyuiCaptionTouched.value = Boolean(stored);
    }
    fetchCharacters();
    fetchComfyWorkflows();
  }
});

watch(validComfyWorkflows, (workflows) => {
  const list = Array.isArray(workflows) ? workflows : [];
  if (!list.length) {
    comfyuiSelectedWorkflow.value = "";
    return;
  }
  const hasSelection = list.some(
    (workflow) => workflow?.name === comfyuiSelectedWorkflow.value,
  );
  if (!hasSelection) {
    comfyuiSelectedWorkflow.value = list[0].name;
  }
});

watch([comfyuiSelectedWorkflow, selectedComfyUsesCaption], () => {
  if (!selectedComfyUsesCaption.value) {
    comfyuiCaption.value = "";
    comfyuiCaptionTouched.value = false;
    comfyuiCaptionFocused.value = false;
    return;
  }
  const stored = loadComfyuiPromptFromSession();
  if (stored != null) {
    comfyuiCaption.value = stored;
    comfyuiCaptionTouched.value = Boolean(stored);
    comfyuiCaptionFocused.value = false;
  } else if (!comfyuiCaptionTouched.value) {
    comfyuiCaption.value = "";
  }
});

watch(comfyuiCaption, () => {
  persistComfyuiPromptToSession();
});

watch(comfyuiMenuOpen, (value) => {
  if (value) {
    // A freshly-opened menu must never inherit a pending close from a prior run.
    clearComfyuiCloseTimer();
    comfyuiRunError.value = "";
    comfyuiRunSuccess.value = "";
    comfyuiCaptionFocused.value = false;
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

async function fetchComfyWorkflows() {
  if (comfyuiWorkflowLoading.value) return;
  comfyuiWorkflowLoading.value = true;
  comfyuiWorkflowError.value = "";
  try {
    const body = await listWorkflows();
    const workflows = body?.workflows;
    comfyuiWorkflows.value = Array.isArray(workflows) ? workflows : [];
  } catch (err) {
    comfyuiWorkflowError.value =
      errorDetail(err) || err?.message || String(err);
    comfyuiWorkflows.value = [];
  } finally {
    comfyuiWorkflowLoading.value = false;
  }
}

async function runComfyWorkflow() {
  if (!canRunComfyWorkflow.value) return;
  comfyuiRunLoading.value = true;
  comfyuiRunError.value = "";
  comfyuiRunSuccess.value = "";
  try {
    const payload = {
      picture_id: image.value.id,
      workflow_name: comfyuiSelectedWorkflow.value,
      caption: comfyuiCaption.value || "",
      client_id: comfyuiClientId.value || undefined,
      stack: stackI2IOutputs.value,
    };
    const body = await runImageToImage(payload, {
      baseUrl: backendUrl.value,
    });
    const promptCount = Array.isArray(body?.prompts) ? body.prompts.length : 0;
    emit("comfyui-run", {
      prompts: Array.isArray(body?.prompts) ? body.prompts : [],
      pictureId: payload.picture_id ?? null,
    });
    comfyuiRunSuccess.value = promptCount
      ? `Queued ${promptCount} run(s) in ComfyUI.`
      : "Queued in ComfyUI.";
    // Show the success message briefly, then close the menu.
    clearComfyuiCloseTimer();
    comfyuiCloseTimer = setTimeout(() => {
      comfyuiCloseTimer = null;
      comfyuiMenuOpen.value = false;
    }, 1200);
  } catch (err) {
    comfyuiRunError.value = errorDetail(err) || err?.message || String(err);
  } finally {
    comfyuiRunLoading.value = false;
  }
}

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

  const orderedIds = sortStackMembers(
    Array.from(imageById.values()),
  ).map((img) => String(img.id));
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

watch(image, (newImage, oldImage) => {
  if (newImage?.id === oldImage?.id) return;
  comfyuiCaptionTouched.value = false;
  comfyuiCaption.value = "";
});

watch(open, (isOpen) => {
  if (!isOpen) {
    descriptionPanelRef.value?.cancelEditDescription();
    descriptionPanelRef.value?.resetCopyState();
    closeFallbackSaveDialog();
  }
});

function resetComfyState() {
  comfyuiMenuOpen.value = false;
  comfyuiRunLoading.value = false;
  comfyuiRunError.value = "";
  comfyuiRunSuccess.value = "";
  comfyuiCaptionTouched.value = false;
  comfyuiCaption.value = "";
}

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

function showPrevImage() {
  return navigateOverlayImage(-1);
}

function navigateOverlayImage(direction, options = {}) {
  const sorted = filmstripImages.value;
  const allowWrap = options?.wrap !== false;
  if (!image.value || !sorted.length) return;
  const idx = filmstripIndexById.value.get(String(image.value.id)) ?? -1;
  if (idx === -1) return;
  let nextIdx = idx + direction;
  if (allowWrap) {
    nextIdx = (nextIdx + sorted.length) % sorted.length;
  } else {
    nextIdx = Math.min(sorted.length - 1, Math.max(0, nextIdx));
    if (nextIdx === idx) {
      return false;
    }
  }
  setOverlayImageById(sorted[nextIdx]?.id ?? null);
  return true;
}

function selectImageByIndex(idx) {
  const list = filmstripImages.value;
  if (!Array.isArray(list)) return;
  const target = list[idx];
  if (target) {
    setOverlayImageById(target.id ?? null);
  }
}

function onFilmstripNavigate(direction) {
  if (isMobile.value) return;
  handleUserActivity();
  const moved = navigateOverlayImage(direction, { wrap: false });
  if (moved === false) {
    filmstripRef.value?.resetWheel?.();
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
  const selection = typeof window === "undefined" ? null : window.getSelection?.();
  return Boolean(selection && !selection.isCollapsed && selection.toString());
}

function handleKeydown(e) {
  if (!open.value) return;
  // Prevent other window keydown listeners (e.g. ImageGrid) from seeing this
  // event while the overlay is open. ImageOverlay mounts before ImageGrid so
  // its handler runs first; without this, ImageGrid would process the same
  // keypress (e.g. Escape) after the overlay has already handled it.
  e.stopImmediatePropagation();

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
    if (commandKey === "z" && !e.shiftKey) {
      e.preventDefault();
      operationStore.undo();
      return;
    }
    if (commandKey === "y" || (commandKey === "z" && e.shiftKey)) {
      e.preventDefault();
      operationStore.redo();
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
      if (!isReadOnly.value) {
        sidebarOpen.value = true;
        nextTick(() => tagsPanelRef.value?.beginAddTag());
      }
      return;
    }
    // All other keys: ignore while chrome is hidden
    return;
  }

  handleUserActivity();

  if (comfyuiCaptionFocused.value) {
    if (e.key === "Escape") {
      if (comfyuiMenuOpen.value) {
        comfyuiMenuOpen.value = false;
      }
      e.preventDefault();
    }
    return;
  }

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
  // Still allow ESC to close the plugin/comfyui menu if open.
  const target = e.target;
  if (isEditableElement(target)) {
    if (e.key === "Escape") {
      if (pluginMenuOpen.value) {
        pluginMenuOpen.value = false;
        e.preventDefault();
      } else if (comfyuiMenuOpen.value) {
        comfyuiMenuOpen.value = false;
        e.preventDefault();
      } else if (target.tagName === "SELECT") {
        // Close the select dropdown on ESC, since it doesn't do that by default.
        target.blur();
      } else {
        return;
      }
    } else {
      return;
    }
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
    if (drawMode.value) {
      clearDrawMode();
    } else if (pluginMenuOpen.value) {
      pluginMenuOpen.value = false;
    } else if (comfyuiMenuOpen.value) {
      comfyuiMenuOpen.value = false;
    } else {
      emit("close");
    }
  } else if (["ArrowLeft", "Left", "ArrowUp", "Up"].includes(e.key)) {
    showPrevImage();
  } else if (["ArrowRight", "Right", "ArrowDown", "Down"].includes(e.key)) {
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
  } else if ((e.key === "t" || e.key === "T") && sidebarOpen.value) {
    if (!isReadOnly.value) {
      e.preventDefault();
      tagsPanelRef.value?.beginAddTag();
    }
  } else if (["1", "2", "3", "4", "5"].includes(e.key)) {
    if (!isReadOnly.value) {
      const score = parseInt(e.key, 10);
      if (image.value) setScore(score);
    }
  }
}

const showFaceBbox = ref(false);
const showDetections = ref(false);
const isMobile = ref(false);
const MOBILE_BREAKPOINT = 900;
const FILMSTRIP_VISIBLE_COUNT = 7;
const FILMSTRIP_BUFFER_COUNT = 3;
const FILMSTRIP_GAP = 0;
const FILMSTRIP_RAIL_PADDING = 8;
const windowHeight = ref(0);
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
    windowHeight.value = window.innerHeight || 0;
  }
}

const filmstripThumbSizePx = computed(() => {
  const targetCount = FILMSTRIP_VISIBLE_COUNT;
  const railPaddingTotal = FILMSTRIP_RAIL_PADDING * 2;
  const railHeight = filmstripRef.value?.railEl?.offsetHeight || 0;
  const overlayMainHeight = overlayMainRef.value?.offsetHeight || 0;
  const fallbackHeight = Math.max(0, windowHeight.value || 0);
  const availableRaw = Math.max(0, overlayMainHeight || fallbackHeight);
  const available = railHeight > 0 ? railHeight : Math.max(0, availableRaw);
  const usable = Math.max(0, available - railPaddingTotal);
  const totalGaps = FILMSTRIP_GAP * (targetCount - 1);
  const rawSize = (usable - totalGaps) / targetCount;
  const computed = Number.isFinite(rawSize) ? Math.floor(rawSize) : 0;
  return computed > 0 ? Math.max(36, computed - 8) : 80;
});

const filmstripStyleVars = computed(() => {
  const railPaddingTotal = FILMSTRIP_RAIL_PADDING * 2;
  const thumbSize = filmstripThumbSizePx.value;
  const railHeight = filmstripRef.value?.railEl?.offsetHeight || 0;
  const overlayMainHeight = overlayMainRef.value?.offsetHeight || 0;
  const fallbackHeight = Math.max(0, windowHeight.value || 0);
  const availableRaw = Math.max(0, overlayMainHeight || fallbackHeight);
  const available = railHeight > 0 ? railHeight : Math.max(0, availableRaw);
  const railWidth = thumbSize + 12;
  return {
    "--filmstrip-thumb-size": `${thumbSize}px`,
    "--filmstrip-rail-width": `${railWidth}px`,
    "--filmstrip-available-height": `${available}px`,
    "--filmstrip-gap": `${FILMSTRIP_GAP}px`,
    "--filmstrip-padding": `${FILMSTRIP_RAIL_PADDING}px`,
    "--filmstrip-padding-total": `${railPaddingTotal}px`,
  };
});

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

function handleBackdropClick() {
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

function handleMouseActivity() {
  if (chromeHidden.value) return; // mouse movement never reveals chrome
  if (Date.now() - lastTouchEndTime < 600) return;
  handleUserActivity();
}

function handleWheelActivity() {
  // Scroll always reveals chrome (deliberate interaction)
  handleUserActivity();
}

function handleOverlayPointerDown() {
  menuWasOpenOnPointerDown = !!(
    document.querySelector(".v-overlay--active") ||
    document.querySelector(".add-to-set.open") ||
    document.querySelector(".add-to-project.open") ||
    // Our own right-click context menu (teleported to <body>). It closes itself
    // on this pointerdown, so it must be detected now, not at click time.
    document.querySelector(".image-ctx-menu")
  );
}

function handleOverlayClick(event) {
  if (touchTapConsumed) {
    touchTapConsumed = false;
    return;
  }
  const target = event?.target;
  if (!target || !(target instanceof HTMLElement)) {
    handleUserActivity();
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
  const interactiveSelector =
    "button, a, input, select, textarea, label, summary, details";
  const interactiveContainerSelector =
    ".overlay-sidebar, .overlay-rail, .overlay-nav";
  if (
    target.closest(interactiveSelector) ||
    target.closest(interactiveContainerSelector)
  ) {
    handleUserActivity();
    return;
  }
  chromeHidden.value = true;
}

function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value;
  if (sidebarOpen.value) {
    chromeHidden.value = false;
  } else {
    handleUserActivity();
  }
}

function openSidebarFromTeaser() {
  if (!image.value) return;
  sidebarOpen.value = true;
  chromeHidden.value = false;
  descriptionPanelRef.value?.startEditDescription();
}

/** The pointer position relative to the zoom viewport (`.overlay-canvas`),
 * the anchor space every zoom change works in. */
function canvasCursorFromEvent(event) {
  const rect = overlayCanvasRef.value?.getBoundingClientRect?.();
  if (!rect || !Number.isFinite(event?.clientX)) return null;
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

/** Z and the toolbar button: toggle between the snap stops, fit ↔ 100%,
 * centre-anchored. */
function toggleZoomSnap() {
  zoom.toggleSnap();
}

/** Double-click: the same fit ↔ 100% toggle, anchored at the click point. */
function onCanvasDblClick(event) {
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

/** Continuous cursor-anchored wheel zoom (the shared model): every step is
 * exponential in the normalized delta, the image point under the pointer
 * stays stationary, and wheel-out clamps hard at fit - no exit. */
function onWheelZoom(event) {
  if (!open.value) return;
  handleUserActivity();
  zoom.wheelZoom(event, canvasCursorFromEvent(event));
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

const filmstripCanvasData = computed(() => {
  const images = filmstripImages.value;
  if (!images.length || !image.value) {
    return { items: [], topBufferSlots: 0 };
  }
  const currentIndex = filmstripIndexById.value.get(String(image.value.id));
  const safeCurrentIndex =
    Number.isFinite(currentIndex) && currentIndex >= 0 ? currentIndex : -1;
  if (safeCurrentIndex === -1) {
    return { items: [], topBufferSlots: 0 };
  }

  const visibleCount = Math.min(
    isMobile.value ? 5 : FILMSTRIP_VISIBLE_COUNT,
    images.length,
  );
  let visibleStart = safeCurrentIndex - Math.floor(visibleCount / 2);
  let visibleEnd = visibleStart + visibleCount - 1;
  if (visibleStart < 0) {
    visibleEnd += Math.abs(visibleStart);
    visibleStart = 0;
  }
  if (visibleEnd >= images.length) {
    const overshoot = visibleEnd - (images.length - 1);
    visibleStart = Math.max(0, visibleStart - overshoot);
    visibleEnd = images.length - 1;
  }

  const bufferCount = isMobile.value ? 0 : FILMSTRIP_BUFFER_COUNT;
  const canvasCount = Math.min(visibleCount + bufferCount * 2, images.length);
  let canvasStart = Math.max(0, visibleStart - bufferCount);
  let canvasEnd = Math.min(images.length - 1, visibleEnd + bufferCount);
  while (canvasEnd - canvasStart + 1 < canvasCount && canvasStart > 0) {
    canvasStart -= 1;
  }
  while (
    canvasEnd - canvasStart + 1 < canvasCount &&
    canvasEnd < images.length - 1
  ) {
    canvasEnd += 1;
  }

  const topBufferSlots = Math.max(0, visibleStart - canvasStart);
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
    const isStackJoined =
      isStackExpanded && !!stackId && stackId === prevStackId;

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
      isStackJoined,
      thumbSrc,
      isVideo: isSupportedVideoFile(getOverlayFormat(item)),
      stackBadgeVisible: getOverlayStackCount(item) > 1,
      isStackLead: stackId !== prevStackId,
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

  return { items, topBufferSlots };
});

const filmstripCanvasWindow = computed(() => filmstripCanvasData.value.items);

const filmstripCanvasStyle = computed(() => {
  if (isMobile.value) return {};
  const slot = filmstripThumbSizePx.value + FILMSTRIP_GAP;
  const offset = Math.max(0, filmstripCanvasData.value.topBufferSlots) * slot;
  return {
    transform: `translateY(-${offset}px)`,
  };
});

function toggleFaceBbox() {
  showFaceBbox.value = !showFaceBbox.value;
}

function toggleDetections() {
  showDetections.value = !showDetections.value;
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
  showFaceBbox.value = true;
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
      (rotateMetadataInFlightImageIds.has(String(id)) ||
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
let overlayResizeObserver = null;
let mediaResizeObserver = null;

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
  const containerWidth = canvas?.clientWidth || 0;
  const containerHeight = canvas?.clientHeight || 0;
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
  document.addEventListener("keydown", onCreatePersonKeydownCapture, true);
  window.addEventListener("pointerdown", handleOverlayPointerDown, true);
  if (typeof ResizeObserver !== "undefined" && overlayMainRef.value) {
    overlayResizeObserver = new ResizeObserver(() => {
      scheduleOverlayDimsUpdate();
    });
    overlayResizeObserver.observe(overlayMainRef.value);
  }
  if (typeof ResizeObserver !== "undefined" && mediaInnerRef.value) {
    mediaResizeObserver = new ResizeObserver(() => {
      scheduleOverlayDimsUpdate();
    });
    mediaResizeObserver.observe(mediaInnerRef.value);
  }
});
onUnmounted(() => {
  window.removeEventListener("resize", updateViewportMetrics);
  window.removeEventListener("keydown", handleKeydown);
  document.removeEventListener("keydown", onCreatePersonKeydownCapture, true);
  window.removeEventListener("pointerdown", handleOverlayPointerDown, true);
  clearComfyuiCloseTimer();
  if (overlayResizeObserver) {
    overlayResizeObserver.disconnect();
    overlayResizeObserver = null;
  }
  if (mediaResizeObserver) {
    mediaResizeObserver.disconnect();
    mediaResizeObserver = null;
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
      ".overlay-sidebar, .overlay-rail, .overlay-nav";
    if (
      target &&
      (target.closest(interactiveSelector) ||
        target.closest(interactiveContainerSelector))
    ) {
      handleUserActivity();
      return;
    }
    if (chromeHidden.value) {
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

function dedupeDetections(items) {
  if (!Array.isArray(items)) return [];
  const seen = new Set();
  const result = [];
  for (const item of items) {
    const id = item?.id;
    const bbox = Array.isArray(item?.bbox) ? item.bbox.join(",") : "";
    const frame = item?.frame_index ?? "";
    const key = id != null ? `id:${id}` : `bbox:${frame}:${bbox}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(item);
  }
  return result;
}

async function fetchComfyWorkflow(imageId) {
  if (!imageId || !backendUrl.value) return;
  const requestId = (comfyWorkflowRequestId += 1);
  const requestedImageId = imageId;
  try {
    const data = await getPictureWorkflow(imageId, {
      baseUrl: backendUrl.value,
    });
    if (comfyWorkflowRequestId !== requestId) return;
    if (!image.value || image.value.id !== requestedImageId) return;
    comfyMetadata.value = data
      ? {
          workflow: data.workflow,
          isApiFormat: data.is_api_format,
          summary: data.summary,
          positive_prompt: data.positive_prompt || null,
          models: data.models || [],
          loras: data.loras || [],
        }
      : null;
  } catch (e) {
    // 404 is expected when no ComfyUI workflow is embedded
    if (e?.response?.status !== 404) {
      console.error("Failed to fetch ComfyUI workflow:", e);
    }
    if (comfyWorkflowRequestId !== requestId) return;
    comfyMetadata.value = null;
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
    // wrong for these two: `orientation` is what `mediaVersion` builds the
    // cache-buster from, so keeping a stale copy leaves `fullImageSrc` pointing
    // at the file the `<img>` has already decoded. An in-place rotate moves it
    // and nothing else, which is exactly the case a local-wins merge would
    // swallow whole.
    for (const field of ["pixel_sha", "orientation"]) {
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

function getFaceThumbStyle(face, idx) {
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
      width: `${FACE_THUMB_BASE}px`,
      height: `${FACE_THUMB_BASE}px`,
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
    Math.max(FACE_THUMB_MIN, FACE_THUMB_BASE),
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

// Per-face menu instances, so focus can return to the one that opened the
// dialog. A plain Map (not a ref): nothing renders from it.
const faceMenuRefs = new Map();

function setFaceMenuRef(key, el) {
  if (el) faceMenuRefs.set(key, el);
  else faceMenuRefs.delete(key);
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
  nextTick(() => faceMenuRefs.get(key)?.focusTrigger?.());
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
const facesCollapsed = ref(false);

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
    }
  },
  { immediate: true },
);

watch(
  () => tagUpdate.value,
  (payload) => {
    if (!payload || typeof payload !== "object") return;
    const nextKey = payload.key || 0;
    if (!nextKey || nextKey === lastTagUpdateKey.value) return;
    lastTagUpdateKey.value = nextKey;
    if (!open.value || !image.value?.id) return;
    const pictureIds = Array.isArray(payload.pictureIds)
      ? payload.pictureIds.map((id) => String(id))
      : [];
    const currentId = String(image.value.id);
    if (pictureIds.length && !pictureIds.includes(currentId)) return;
    fetchOverlayMetadata(image.value.id);
  },
);

watch(
  () => descriptionUpdate.value,
  (payload) => {
    if (!payload || typeof payload !== "object") return;
    const nextKey = payload.key || 0;
    if (!nextKey || nextKey === lastDescriptionUpdateKey.value) return;
    lastDescriptionUpdateKey.value = nextKey;
    if (!open.value || !image.value?.id) return;
    const pictureIds = Array.isArray(payload.pictureIds)
      ? payload.pictureIds.map((id) => String(id))
      : [];
    const currentId = String(image.value.id);
    if (pictureIds.length && !pictureIds.includes(currentId)) return;
    fetchOverlayMetadata(image.value.id);
  },
);

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
watch(
  () => smartScoreUpdate.value,
  (payload) => {
    if (!payload || typeof payload !== "object") return;
    const nextKey = payload.key || 0;
    if (!nextKey || nextKey === lastSmartScoreUpdateKey.value) return;
    lastSmartScoreUpdateKey.value = nextKey;
    if (!open.value || !image.value?.id) return;
    fetchOverlayMetadata(image.value.id);
  },
);

// A Segment run finished. The boxes come from /pictures/{id}/detections, which
// is only read when the displayed card changes, so without this the overlay kept
// the pre-segment boxes until it was closed and reopened. Re-fetch on every
// distinct signal rather than gating on the payload's ids: two detection tasks
// completing in one Vue flush coalesce to the later payload, which can omit the
// open card. fetchDetections is requestId-deduped and drops a response for a card
// that is no longer displayed, so a redundant fetch is one cheap call, not a race.
watch(
  () => detectionUpdate.value,
  (payload) => {
    if (!payload || typeof payload !== "object") return;
    const nextKey = payload.key || 0;
    if (!nextKey || nextKey === lastDetectionUpdateKey.value) return;
    lastDetectionUpdateKey.value = nextKey;
    if (!open.value || !image.value?.id) return;
    fetchDetections(image.value.id);
  },
);

const faceAssignItems = computed(() => {
  const faces = Array.isArray(faceBboxes.value) ? faceBboxes.value : [];
  return faces.map((face, idx) => ({
    ...face,
    faceIdx: idx,
    faceKey: face?.id ?? `face-${idx}`,
    label: `Face ${idx + 1}`,
  }));
});

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
  return (
    errorDetail(err) || err?.message || String(err || fallback) );
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
      if (!choice?.saveId) throw new Error("The desktop save dialog did not return a save request.");
      try {
        const blob = await fetchOriginalMedia(info);
        const result = await desktop.completeMediaSaveAs(
          choice.saveId,
          await blob.arrayBuffer(),
        );
        if (!result?.saved) throw new Error("The desktop app did not write the file.");
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
      reason:
        "This browser cannot copy image pixels. Save the media instead.",
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
  if (!blob) throw new Error("This browser could not encode the pixels as PNG.");
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
      if (!result?.copied) throw new Error("The desktop clipboard rejected the image.");
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

defineExpose({ saveMedia, saveMediaAs, copyMedia });
</script>
<style scoped src="./ImageOverlay.css"></style>
