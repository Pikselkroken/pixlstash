<template>
  <div class="rf-overlay" @click.self="emit('close')">
    <div class="rf-shell">
      <!-- Toolbar: shared OverlayToolbar shell (same chrome as ImageOverlay) hosting
           the review-specific controls. Close + title live in the shell; the tag
           picker, rescan, scope filters, clear-filters and progress go in #actions. -->
      <OverlayToolbar @close="emit('close')">
        <template #title>Review tags</template>

        <template #actions>
          <span class="rf-divider" aria-hidden="true"></span>

          <!-- Custom tag picker over the whole vault tag list, modelled on AddToEntityControl:
               a compact trigger button (current tag + chevron) opens a dropdown panel with an
               autofocused search input above a scrollable, alphabetised list. Typing filters by
               substring; anomaly (smart-score-penalised) tags render red; tags with pending
               suggestions show their count. Picking one runs select-or-scan so the queue always
               switches to it. The panel is position:fixed from the trigger's rect so it escapes
               the topbar's clip and sits above the overlay. The icon-button re-scans the active tag. -->
          <div class="rf-field rf-field--tag">
            <span class="rf-field-label">Tag</span>
            <button
              class="rf-penalised-toggle"
              :class="{ 'rf-penalised-toggle--on': penalisedOnly }"
              type="button"
              :aria-pressed="penalisedOnly"
              title="Only list smart-score penalised tags in the picker"
              @click="penalisedOnly = !penalisedOnly"
            >
              <v-icon size="13">mdi-alert-octagon-outline</v-icon>
              Penalised only
            </button>
            <div ref="tagPickRef" class="rf-tag-pick">
              <button
                class="rf-control rf-tag-trigger"
                type="button"
                :disabled="store.scanning"
                :aria-expanded="tagMenuOpen"
                aria-haspopup="listbox"
                aria-label="Pick a tag"
                @click.stop="toggleTagMenu"
              >
                <span
                  class="rf-tag-trigger-label"
                  :class="{
                    'rf-tag-anomaly':
                      store.activeTag && store.isAnomalyTag(store.activeTag),
                    'rf-tag-trigger-label--placeholder': !store.activeTag,
                  }"
                >
                  {{ store.activeTag || "Pick a tag" }}
                </span>
                <v-icon size="14" class="rf-tag-chevron"
                  >mdi-chevron-down</v-icon
                >
              </button>

              <div
                v-if="tagMenuOpen"
                ref="tagMenuRef"
                class="rf-tag-menu"
                role="listbox"
                :style="tagMenuStyle"
              >
                <div class="rf-tag-search">
                  <v-icon size="14">mdi-magnify</v-icon>
                  <input
                    ref="tagSearchRef"
                    v-model="tagQuery"
                    type="text"
                    placeholder="search tags…"
                    @keydown.escape.stop.prevent="closeTagMenu"
                    @keydown.down.stop.prevent="moveTagHighlight(1)"
                    @keydown.up.stop.prevent="moveTagHighlight(-1)"
                    @keydown.enter.stop.prevent="pickHighlightedTag"
                  />
                </div>
                <div class="rf-tag-list">
                  <div v-if="!filteredTags.length" class="rf-tag-empty">
                    No tags found
                  </div>
                  <button
                    v-for="(opt, idx) in filteredTags"
                    :key="opt.tag"
                    class="rf-tag-item"
                    :class="{ 'rf-tag-item--active': idx === tagHighlight }"
                    type="button"
                    role="option"
                    @mouseenter="tagHighlight = idx"
                    @click.stop="onPickTag(opt.tag)"
                  >
                    <span :class="{ 'rf-tag-anomaly': opt.anomaly }">
                      {{ opt.tag }}
                    </span>
                    <span v-if="opt.pending" class="rf-tag-count">
                      · {{ opt.pending }}
                    </span>
                  </button>
                </div>
              </div>
            </div>
            <button
              class="overlay-icon-btn rf-rescan"
              type="button"
              :disabled="!store.activeTag || store.scanning"
              :title="
                store.activeTag
                  ? `Re-scan “${store.activeTag}” for near-neighbour disagreements`
                  : 'Pick a tag to scan'
              "
              @click="store.rescanActiveTag()"
            >
              <v-icon size="16" :class="{ 'mdi-spin': store.scanning }">
                {{ store.scanning ? "mdi-loading" : "mdi-refresh" }}
              </v-icon>
            </button>
          </div>
          <span v-if="store.scanError" class="rf-scan-error">{{
            store.scanError
          }}</span>

          <span class="rf-spacer" aria-hidden="true"></span>

          <!-- Scope filters: narrow the queue to a project / set / character. Each "Any"
               option clears that dimension; changes re-run summary/queue/bulk via setScope. -->
          <label class="rf-field">
            <span class="rf-field-label">Project</span>
            <select
              class="rf-control rf-select rf-select--scope"
              :value="store.scope.projectId ?? ''"
              @change="onScopeChange('projectId', $event.target.value, $event)"
            >
              <option value="">Any</option>
              <option v-for="p in store.projects" :key="p.id" :value="p.id">
                {{ p.name || `Project ${p.id}` }}
              </option>
            </select>
          </label>

          <label class="rf-field">
            <span class="rf-field-label">Set</span>
            <select
              class="rf-control rf-select rf-select--scope"
              :value="store.scope.setId ?? ''"
              @change="onScopeChange('setId', $event.target.value, $event)"
            >
              <option value="">Any</option>
              <option v-for="s in store.sets" :key="s.id" :value="s.id">
                {{ s.name || `Set ${s.id}` }}
              </option>
            </select>
          </label>

          <label class="rf-field">
            <span class="rf-field-label">Character</span>
            <select
              class="rf-control rf-select rf-select--scope"
              :value="store.scope.characterId ?? ''"
              @change="
                onScopeChange('characterId', $event.target.value, $event)
              "
            >
              <option value="">Any</option>
              <option value="UNASSIGNED">Unassigned</option>
              <option v-for="c in store.characters" :key="c.id" :value="c.id">
                {{ c.name || `Character ${c.id}` }}
              </option>
            </select>
          </label>

          <div class="rf-progress">
            <span class="rf-progress-remaining"
              >{{ store.remainingTotal }} left</span
            >
            <span class="rf-progress-tally">
              <span class="rf-tally rf-tally--removed"
                >✗ {{ store.removedCount }}</span
              >
              <span class="rf-tally rf-tally--added"
                >+ {{ store.addedCount }}</span
              >
              <span class="rf-tally rf-tally--kept"
                >✓ {{ store.keptCount }}</span
              >
            </span>
          </div>
        </template>
      </OverlayToolbar>

      <!-- Body -->
      <section class="rf-body">
        <div v-if="store.loading" class="rf-state">Loading…</div>

        <div v-else-if="store.error" class="rf-state rf-state--error">
          {{ store.error }}
          <button class="rf-control rf-retry" @click="store.fetchQueue()">
            Retry
          </button>
        </div>

        <div v-else-if="!current" class="rf-state rf-state--done">
          <v-icon size="40">mdi-check-all</v-icon>
          <p v-if="!store.activeTag">
            No suggestions{{
              hasScopeFilter ? " match the current filters" : ""
            }}.
          </p>
          <p v-else>
            All caught up on “{{ store.activeTag }}”{{
              hasScopeFilter ? " for the current filters" : ""
            }}.
          </p>
          <p class="rf-state-sub">
            <template v-if="hasScopeFilter">
              Widen the filters or pick another tag above, or close to head back
              to the grid.
            </template>
            <template v-else>
              Pick another tag above, or close to head back to the grid.
            </template>
          </p>
        </div>

        <div v-else class="rf-review">
          <!-- Combined prompt + guidance banner: the verdict bucket and the review
               question are merged into one strip. The bucket's accent colours the
               icon, the 4px left border, a tint of the fill, and the title pill;
               with no tagger prediction it falls back to a neutral banner. -->
          <div
            class="rf-banner"
            :class="
              currentBucket
                ? `rf-banner--${currentBucket.key}`
                : 'rf-banner--neutral'
            "
          >
            <v-icon :size="26" class="rf-banner-icon">{{
              currentBucket ? currentBucket.icon : "mdi-help-circle-outline"
            }}</v-icon>
            <div class="rf-banner-text">
              <div class="rf-banner-title">
                The left is tagged “{{ current.tag }}” — but is it?
              </div>
              <div class="rf-banner-meaning">{{ bannerMeaning }}</div>
            </div>
            <div v-if="currentBucket" class="rf-banner-aside">
              <span class="rf-banner-pill">{{ currentBucket.title }}</span>
              <span class="rf-banner-count"
                >{{ bucketRemaining }} left in this group</span
              >
            </div>
          </div>

          <div class="rf-pair">
            <figure
              class="rf-pane rf-pane--flagged"
              :class="{ 'rf-pane--selected': isPaneSelected(taggedSide.id) }"
            >
              <figcaption class="rf-pane-head">
                <span class="rf-pane-id">#{{ taggedSide.id }}</span>
                <span class="rf-pane-pred">
                  <span class="rf-pane-pred-label rf-pane-pred-label--has">
                    <v-icon size="13">mdi-tag</v-icon>
                    Predicted “{{ current.tag }}”
                  </span>
                  <span class="rf-pane-conf"
                    >· {{ confText(true, taggedSide.conf) }}</span
                  >
                </span>
              </figcaption>
              <div class="rf-img-wrap">
                <img
                  class="rf-img"
                  :src="imgSrc(taggedSide.id, taggedSide.ext)"
                  :alt="`picture ${taggedSide.id}`"
                  title="Click to select · scroll to zoom"
                  @click="togglePaneSelection(taggedSide.id)"
                  @wheel.prevent="openZoom(taggedSide.id, taggedSide.ext)"
                  @error="onImgError($event, taggedSide.id)"
                />
                <button
                  type="button"
                  class="rf-zoom-btn"
                  title="Zoom"
                  @click.stop="openZoom(taggedSide.id, taggedSide.ext)"
                >
                  <v-icon size="16">mdi-magnify-plus-outline</v-icon>
                </button>
                <span class="rf-pane-check" aria-hidden="true">
                  <v-icon size="16">mdi-check-bold</v-icon>
                </span>
              </div>
              <figcaption
                v-if="voteHint(taggedSide.id)"
                class="rf-pane-foot"
              >
                <span class="rf-vote-hint">{{ voteHint(taggedSide.id) }}</span>
              </figcaption>
            </figure>

            <figure
              class="rf-pane"
              :class="{ 'rf-pane--selected': isPaneSelected(untaggedSide.id) }"
            >
              <figcaption class="rf-pane-head">
                <span class="rf-pane-id">#{{ untaggedSide.id }}</span>
                <span class="rf-pane-pred">
                  <span class="rf-pane-pred-label">Not tagged</span>
                  <span class="rf-pane-conf"
                    >· {{ confText(false, untaggedSide.conf) }}</span
                  >
                </span>
              </figcaption>
              <div class="rf-img-wrap">
                <img
                  class="rf-img"
                  :src="imgSrc(untaggedSide.id, untaggedSide.ext)"
                  :alt="`picture ${untaggedSide.id}`"
                  title="Click to select · scroll to zoom"
                  @click="togglePaneSelection(untaggedSide.id)"
                  @wheel.prevent="openZoom(untaggedSide.id, untaggedSide.ext)"
                  @error="onImgError($event, untaggedSide.id)"
                />
                <button
                  type="button"
                  class="rf-zoom-btn"
                  title="Zoom"
                  @click.stop="openZoom(untaggedSide.id, untaggedSide.ext)"
                >
                  <v-icon size="16">mdi-magnify-plus-outline</v-icon>
                </button>
                <span class="rf-pane-check" aria-hidden="true">
                  <v-icon size="16">mdi-check-bold</v-icon>
                </span>
              </div>
              <figcaption
                v-if="voteHint(untaggedSide.id)"
                class="rf-pane-foot"
              >
                <span class="rf-vote-hint">{{ voteHint(untaggedSide.id) }}</span>
              </figcaption>
            </figure>
          </div>

          <div class="rf-actions">
            <span class="rf-actions-label"
              >Which really has “{{ current.tag }}”?</span
            >
            <button
              class="rf-action rf-action--leftonly"
              :class="{
                'rf-action--rec':
                  verdict.strong && verdict.corner === 'leftonly',
              }"
              type="button"
              title="The flag is right and the twin is clean — only the left has it. No change."
              @click="markLeftOnly"
            >
              <kbd>L</kbd> Left only
              <span
                v-if="verdict.strong && verdict.corner === 'leftonly'"
                class="rf-rec-pip"
                >likely</span
              >
            </button>
            <button
              class="rf-action rf-action--both"
              :class="{
                'rf-action--rec': verdict.strong && verdict.corner === 'both',
              }"
              type="button"
              title="Both have it — tag the right one too."
              @click="markBoth"
            >
              <kbd>B</kbd> Both
              <span
                v-if="verdict.strong && verdict.corner === 'both'"
                class="rf-rec-pip"
                >likely</span
              >
            </button>
            <button
              class="rf-action rf-action--neither"
              :class="{
                'rf-action--rec':
                  verdict.strong && verdict.corner === 'neither',
              }"
              type="button"
              title="Neither has it — the flag was wrong, clear the left."
              @click="markNeither"
            >
              <kbd>N</kbd> Neither
              <span
                v-if="verdict.strong && verdict.corner === 'neither'"
                class="rf-rec-pip"
                >likely</span
              >
            </button>
            <button
              class="rf-action rf-action--swap"
              :class="{
                'rf-action--rec':
                  verdict.strong && verdict.corner === 'rightonly',
              }"
              type="button"
              title="The left is actually clean and the RIGHT has it — swap: clear the left, tag the right."
              @click="markRightOnly"
            >
              <kbd>R</kbd> Right only ⇄
              <span
                v-if="verdict.strong && verdict.corner === 'rightonly'"
                class="rf-rec-pip"
                >likely</span
              >
            </button>

            <span class="rf-actions-gap"></span>

            <button
              class="rf-action rf-action--skip"
              type="button"
              title="Not sure yet — send to the back of the queue to revisit. No change made."
              @click="skip"
            >
              <kbd>S</kbd> Skip
            </button>
            <button
              class="rf-action rf-action--undo"
              type="button"
              :disabled="!store.canUndo"
              title="Undo the last decision — reopens it and reverses the tag change."
              @click="store.undo()"
            >
              <kbd>U</kbd> Undo
            </button>
          </div>

          <!-- Live consistency guard: shown only when the staged corner contradicts a
               confident prior call on one of these two pictures this session. -->
          <div v-if="pendingDecision" class="rf-confirm" role="alertdialog">
            <span class="rf-confirm-msg">⚠ {{ pendingMessage }}</span>
            <div class="rf-confirm-actions">
              <button
                class="rf-control rf-confirm-btn rf-confirm-btn--apply"
                type="button"
                title="Apply this decision despite the earlier call (Enter)."
                @click="confirmPendingDecision"
              >
                <kbd>↵</kbd> Apply
              </button>
              <button
                class="rf-control rf-confirm-btn"
                type="button"
                title="Leave the card unchanged (Esc)."
                @click="cancelPendingDecision"
              >
                <kbd>Esc</kbd> Cancel
              </button>
            </div>
          </div>

          <!-- Direct tagging of the selected pane(s), independent of the queue
               decision. Anchored bottom-right; the menu opens above the button. -->
          <div ref="tagApplyRef" class="rf-tag-apply">
            <TbTagPanel
              v-if="tagApplyOpen"
              class="rf-tag-apply-panel"
              :backend-url="props.backendUrl"
              :selected-count="selectedPaneIds.length"
              :selected-image-ids="selectedPaneIds"
              :all-grid-images="selectedSideImages"
              :open="tagApplyOpen"
              @tags-applied="onTagsApplied"
              @close="closeTagApply"
            />
            <button
              type="button"
              class="rf-tag-apply-btn"
              :class="{ 'rf-tag-apply-btn--open': tagApplyOpen }"
              title="Apply tags to the selected image(s) (T)"
              @click="tagApplyOpen ? closeTagApply() : openTagApply()"
            >
              <v-icon size="16">mdi-tag-plus-outline</v-icon>
              Apply tags
              <span v-if="selectedPaneIds.length" class="rf-tag-apply-count">{{
                selectedPaneIds.length
              }}</span>
            </button>
          </div>
        </div>
      </section>
    </div>

    <!-- Full-screen zoom: click an image to inspect detail; scroll to magnify, drag to pan. -->
    <div
      v-if="zoom"
      class="rf-zoom"
      :class="{ 'rf-zoom--panning': panning }"
      @wheel.prevent="onZoomWheel"
      @mousedown="onZoomMouseDown"
      @mousemove="onZoomMouseMove"
      @mouseup="onZoomMouseUp"
      @mouseleave="onZoomMouseUp"
      @click="onZoomClick"
    >
      <img
        class="rf-zoom-img"
        :src="zoom.src"
        :style="zoomStyle"
        alt="zoomed image"
        draggable="false"
        @load="onZoomLoad"
      />
      <div
        class="rf-zoom-hint"
        :class="{ 'rf-zoom-hint--hidden': !hintVisible }"
      >
        {{ Math.round(zoomScale * 100) }}% · scroll to zoom · drag to pan ·
        click or Esc to close · <kbd>←</kbd>/<kbd>→</kbd> still apply
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import OverlayToolbar from "../widgets/OverlayToolbar.vue";
import TbTagPanel from "../panels/TbTagPanel.vue";
import { useReviewFixesStore } from "../../stores/useReviewFixesStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useProjectStore } from "../../stores/useProjectStore";

// Sidebar selection sentinels (mirrors useSelectionStore.js / App.vue): these are
// pseudo-views, not real characters, so they never map to a character_id filter.
const ALL_PICTURES_ID = "ALL";
const UNASSIGNED_PICTURES_ID = "UNASSIGNED";
const SCRAPHEAP_PICTURES_ID = "SCRAPHEAP";

const props = defineProps({
  backendUrl: { type: String, default: "" },
});
const emit = defineEmits(["close", "tags-applied"]);

const store = useReviewFixesStore();
const selectionStore = useSelectionStore();
const projectStore = useProjectStore();
const current = computed(() => store.current);

// Translate the app's current sidebar/project selection into the review scope, so the
// overlay opens pre-filtered to whatever view it was launched from. Only dimensions that
// are actually active are set; the rest stay null (unfiltered). The "ALL"/"SCRAPHEAP"
// pseudo-views carry no character filter; "UNASSIGNED" maps to the literal the API expects.
function initialScopeFromSelection() {
  const scope = { projectId: null, setId: null, characterId: null };
  if (projectStore.selectedProjectId != null) {
    scope.projectId = projectStore.selectedProjectId;
  }
  if (selectionStore.selectedSet != null) {
    scope.setId = selectionStore.selectedSet;
  }
  const character = selectionStore.selectedCharacter;
  if (character === UNASSIGNED_PICTURES_ID) {
    scope.characterId = UNASSIGNED_PICTURES_ID;
  } else if (
    character != null &&
    character !== ALL_PICTURES_ID &&
    character !== SCRAPHEAP_PICTURES_ID
  ) {
    // A real (numeric) character id.
    scope.characterId = character;
  }
  return scope;
}

// Any active scope filter? Drives the "Clear filters" affordance.
const hasScopeFilter = computed(() => {
  const { projectId, setId, characterId } = store.scope;
  return projectId != null || setId != null || characterId != null;
});

// Apply a single dropdown change. <select> values are strings: empty = "Any" (null);
// projectId/setId coerce to numbers; characterId keeps the "UNASSIGNED" literal but
// coerces a real id to a number. setScope re-runs the summary/queue/bulk-count fetches.
//
// We blur the <select> after the change so focus doesn't linger on it: a focused native
// <select> swallows the decision keys (L/B/N/R/…) into its own type-ahead, jumping to a set
// whose name starts with that letter. Blurring is the first half of the keyboard-leak fix;
// handleKeyDown no longer bailing on a focused <select> is the second.
function onScopeChange(dimension, raw, event) {
  let value = null;
  if (raw !== "" && raw != null) {
    if (dimension === "characterId") {
      value = raw === "UNASSIGNED" ? "UNASSIGNED" : Number(raw);
    } else {
      value = Number(raw);
    }
  }
  event?.target?.blur();
  store.setScope({ [dimension]: value });
}

// --- Tag picker -------------------------------------------------------------
//
// A custom dropdown over the whole vault (store.allTags), modelled on AddToEntityControl:
// a trigger button opens a panel with an autofocused search box above a scrollable list.
// The list is ALPHABETISED (case-insensitive) and substring-filtered by the search box;
// anomaly (smart-score-penalised) tags render red; tags with pending suggestions show their
// count. The panel is positioned fixed from the trigger's rect (the doc's established
// pattern for popovers inside fixed overlays) so it escapes the topbar clip.
const tagPickRef = ref(null);
const tagMenuRef = ref(null);
const tagSearchRef = ref(null);
const tagMenuOpen = ref(false);
const tagQuery = ref("");
const tagHighlight = ref(0);
// When on, the tag picker lists only smart-score penalised (anomaly) tags —
// the ones most worth reviewing.
const penalisedOnly = ref(false);
const tagMenuStyle = ref({});

// Full vault tag list, alphabetised, each carrying its anomaly flag and pending count.
// Falls back to the summary tags until the background fetchAllTags resolves, so the picker
// is never empty between open and that fetch landing.
const allTagOptions = computed(() => {
  const pendingByTag = new Map(store.tags.map((t) => [t.tag, t.total]));
  const source = store.allTags.length
    ? store.allTags
    : store.tags.map((t) => t.tag);
  return [...source]
    .sort((a, b) =>
      String(a).localeCompare(String(b), undefined, { sensitivity: "base" }),
    )
    .map((tag) => ({
      tag,
      pending: pendingByTag.get(tag) || 0,
      anomaly: store.isAnomalyTag(tag),
    }));
});

// Substring filter (case-insensitive), preserving the alphabetical order above.
const filteredTags = computed(() => {
  const needle = tagQuery.value.trim().toLowerCase();
  let opts = allTagOptions.value;
  if (penalisedOnly.value) opts = opts.filter((opt) => opt.anomaly);
  if (!needle) return opts;
  return opts.filter((opt) => opt.tag.toLowerCase().includes(needle));
});

// Position the panel under the trigger using viewport coordinates (position: fixed), so it
// is not clipped by the topbar and sits above the .rf-overlay (z-index 4000). Mirrors the
// AddToEntityControl flyout's getBoundingClientRect approach; the panel's own z-index (set
// in CSS, 4200) puts it above the overlay and the zoom layer.
function positionTagMenu() {
  const trigger = tagPickRef.value;
  if (!trigger) return;
  const rect = trigger.getBoundingClientRect();
  const menuW = 240;
  const vw = window.innerWidth;
  // Keep the panel on-screen if the trigger sits near the right edge.
  const left = Math.min(rect.left, vw - menuW - 8);
  tagMenuStyle.value = {
    position: "fixed",
    top: `${rect.bottom + 6}px`,
    left: `${Math.max(8, left)}px`,
    width: `${menuW}px`,
  };
}

function toggleTagMenu() {
  if (store.scanning) return;
  if (tagMenuOpen.value) closeTagMenu();
  else openTagMenu();
}

function openTagMenu() {
  tagMenuOpen.value = true;
  tagQuery.value = "";
  tagHighlight.value = 0;
  positionTagMenu();
  // Autofocus the search input on open so typing filters immediately; while it holds focus
  // the overlay's handleKeyDown correctly bails (it's an <input>), so the decision keys
  // don't fire and typing falls through to the filter.
  nextTick(() => tagSearchRef.value?.focus());
  document.addEventListener("pointerdown", handleTagOutsideClick, true);
  window.addEventListener("resize", positionTagMenu);
}

function closeTagMenu() {
  if (!tagMenuOpen.value) return;
  tagMenuOpen.value = false;
  tagQuery.value = "";
  document.removeEventListener("pointerdown", handleTagOutsideClick, true);
  window.removeEventListener("resize", positionTagMenu);
  // Return focus to the body so the L/B/N/R/S/U decision keys work again (the search input
  // is an <input>, which handleKeyDown treats as editable and bails on).
  tagSearchRef.value?.blur();
  if (document.activeElement instanceof HTMLElement)
    document.activeElement.blur();
}

// Click-outside closes the panel (mirrors AddToEntityControl.handleOutsideClick): ignore
// clicks inside the trigger wrapper or the (fixed) panel, close on anything else.
function handleTagOutsideClick(event) {
  const target = event?.target;
  if (!target || !(target instanceof HTMLElement)) return;
  const inPicker =
    tagPickRef.value?.contains(target) || tagMenuRef.value?.contains(target);
  if (!inPicker) closeTagMenu();
  if (tagApplyOpen.value && !tagApplyRef.value?.contains(target)) {
    closeTagApply();
  }
}

function moveTagHighlight(delta) {
  const n = filteredTags.value.length;
  if (!n) return;
  tagHighlight.value = (tagHighlight.value + delta + n) % n;
}

function pickHighlightedTag() {
  const opt = filteredTags.value[tagHighlight.value];
  if (opt) onPickTag(opt.tag);
}

// Pick a tag → always load that tag's queue (select if it has pending suggestions, otherwise
// scan to populate), then close the panel and restore body focus.
function onPickTag(tag) {
  if (!tag) return;
  store.selectOrScan(tag);
  closeTagMenu();
}

// The disagreeing pair always has one tagged and one untagged image. We put the
// tagged one on the LEFT and the untagged one on the RIGHT, so "Left is right" /
// "Right is right" read literally. For a "remove" suggestion the suspect is the
// tagged one; for "add" it's the twin.
function sideSuspect() {
  const i = current.value;
  return { id: i.picture_id, ext: i.picture_ext, conf: i.tagger_confidence };
}
function sideTwin() {
  const i = current.value;
  return {
    id: i.twin_picture_id,
    ext: i.twin_ext,
    conf: i.twin_tagger_confidence,
  };
}
const taggedSide = computed(() =>
  current.value
    ? current.value.direction === "remove"
      ? sideSuspect()
      : sideTwin()
    : null,
);
const untaggedSide = computed(() =>
  current.value
    ? current.value.direction === "remove"
      ? sideTwin()
      : sideSuspect()
    : null,
);

// ── Pane selection + direct tagging ────────────────────────────────────────────
// The reviewer can select one or both panes (click a pane to toggle) and apply
// tags to them directly via the bulk tag menu — independent of the L/B/N/R
// queue decision. Selection resets whenever the pair advances.
const selectedPaneIds = ref([]);

function isPaneSelected(id) {
  return selectedPaneIds.value.includes(id);
}

function togglePaneSelection(id) {
  if (id == null) return;
  selectedPaneIds.value = isPaneSelected(id)
    ? selectedPaneIds.value.filter((x) => x !== id)
    : [...selectedPaneIds.value, id];
}

// Arrow-key selection: ← / → set the selection to that pane; with Shift/Ctrl
// they add the pane to the current selection (so you can grab both).
function selectPaneByArrow(side, additive) {
  const pane = side === "left" ? taggedSide.value : untaggedSide.value;
  if (!pane) return;
  if (additive) {
    if (!isPaneSelected(pane.id)) {
      selectedPaneIds.value = [...selectedPaneIds.value, pane.id];
    }
  } else {
    selectedPaneIds.value = [pane.id];
  }
}

// Reset selection (and close the tag menu) when a new pair loads.
watch(
  () => current.value && current.value.picture_id,
  () => {
    selectedPaneIds.value = [];
    tagApplyOpen.value = false;
  },
);

// Minimal image objects so TbTagPanel can show a thumbnail preview of the panes.
const selectedSideImages = computed(() => {
  const sides = [taggedSide.value, untaggedSide.value].filter(Boolean);
  return sides
    .filter((s) => selectedPaneIds.value.includes(s.id))
    .map((s) => ({ id: s.id, format: s.ext }));
});

const tagApplyOpen = ref(false);
const tagApplyRef = ref(null);

function openTagApply() {
  if (!current.value) return;
  // T with nothing selected is a one-press "tag these": default to both panes.
  if (!selectedPaneIds.value.length) {
    selectedPaneIds.value = [taggedSide.value, untaggedSide.value]
      .filter(Boolean)
      .map((s) => s.id);
  }
  tagApplyOpen.value = true;
}

function closeTagApply() {
  tagApplyOpen.value = false;
}

function onTagsApplied(payload) {
  emit("tags-applied", payload);
}

// The decision is the tagger's per-image confidence (the near-neighbour link only chose
// which pair to compare). See store.decision: it places the pair in one of four corners
// — both / left only / right only / neither — by whether each image clears 50%.
const verdict = computed(() => {
  const item = current.value;
  if (!item) return null;
  const d = store.decision(item);
  if (!d.hasModel) {
    return {
      corner: null,
      strong: false,
      pct: 0,
      sub: "No tagger prediction on these — your call.",
    };
  }
  const pct = Math.min(99, Math.round(d.confidence * 100));
  const strong = d.confidence >= 0.6;
  const phrase = {
    both: "both images have it",
    neither: "the flag is wrong — neither has it",
    leftonly: "the flag is right — only the left has it",
    rightonly: "it's the RIGHT, not the left — swap them",
  }[d.corner];
  const sub = strong
    ? `Tagger thinks ${phrase} (${pct}%).`
    : `Tagger is unsure here (${pct}%) — your call.`;
  return { corner: d.corner, strong, pct, sub };
});

// Group banner: the queue is sorted into verdict buckets (see the store's
// BUCKET_ORDER); this announces which group the reviewer is in and what to do.
const BUCKETS = {
  neither: {
    key: "neither",
    icon: "mdi-close-circle-multiple-outline",
    title: "Neither has it",
    meaning:
      "The tagger thinks the flag is wrong on both near-twins — clear it from the left (N).",
  },
  both: {
    key: "both",
    icon: "mdi-check-circle-multiple-outline",
    title: "Both have it",
    meaning:
      "The tagger is confident both images show this tag — add it to the twin (B).",
  },
  leftonly: {
    key: "leftonly",
    icon: "mdi-arrow-left-bold-box-outline",
    title: "Only the left has it",
    meaning:
      "The flag is right and the near-twin is genuinely clean — no change (L).",
  },
  rightonly: {
    key: "rightonly",
    icon: "mdi-swap-horizontal-bold",
    title: "It's the right one, not the left",
    meaning: "The label is on the wrong image — swap it (R).",
  },
};

const currentBucket = computed(() => {
  const corner = store.decision(current.value)?.corner;
  return corner ? BUCKETS[corner] || null : null;
});

// How many pairs of the current group are still in the loaded queue (this one
// included), so the banner can show "N left in this group".
const bucketRemaining = computed(() => {
  const corner = store.decision(current.value)?.corner;
  if (!corner) return 0;
  return store.items.filter((it) => store.decision(it).corner === corner).length;
});

// One guidance line for the merged banner: the bucket's "what to do" meaning, with
// the tagger-unsure caveat appended when the prediction is weak. With no bucket (no
// tagger prediction) it falls back to the verdict's own sub-line.
const bannerMeaning = computed(() => {
  if (currentBucket.value) {
    const unsure =
      verdict.value && !verdict.value.strong
        ? " · Tagger is unsure here — your call."
        : "";
    return currentBucket.value.meaning + unsure;
  }
  return verdict.value?.sub ?? "";
});

// Compact per-pane confidence phrasing for the pane header. The tagger's raw
// confidence is P(tag applies); we read it from each pane's own point of view so
// it's unambiguous: the flagged (left) pane says "X% sure" when the model agrees
// it has the tag and "X% sure it isn't" when it leans clean. The untagged twin
// (right) just says "X% sure" — the strength of the prediction, without an "it
// is"/"it isn't" qualifier that only muddies an already-untagged image.
function confText(flagged, conf) {
  if (conf === null || conf === undefined) return "no tagger prediction";
  const pct = Math.round(conf * 100);
  const sureNot = conf < 0.5;
  const sure = sureNot ? 100 - pct : pct;
  if (flagged) return sureNot ? `${sure}% sure it isn’t` : `${sure}% sure`;
  return `${sure}% sure`;
}

// --- Decision dispatch + live consistency guard ----------------------------
//
// Every decision routes through attemptDecision(corner): it asks the store whether the
// corner contradicts a confident prior call on either pictured id this session, and if so
// stages it in pendingDecision (an inline confirm bar) instead of dispatching. With no
// conflict it dispatches immediately, exactly as before.
const pendingDecision = ref(null); // { corner, conflict } while awaiting confirm, else null

// Map a corner to its store action. Left is always the flagged image; "both"/"neither"
// flip between accept and fixTwin by direction (keep this mapping in lock-step with the
// store's vote translation). The corner string is threaded through so the store records
// the consistency vote and undo can reverse it.
function dispatchDecision(corner) {
  const i = current.value;
  if (!i) return;
  if (corner === "leftonly") {
    // Flag is right, twin clean — labels already correct, no change.
    store.dismiss("leftonly");
  } else if (corner === "both") {
    // Both have it → tag the (right) untagged image too.
    if (i.direction === "remove") store.fixTwin("both");
    else store.accept("both");
  } else if (corner === "neither") {
    // Neither has it → the flag was wrong, clear the (left) flagged image.
    if (i.direction === "remove") store.accept("neither");
    else store.fixTwin("neither");
  } else if (corner === "rightonly") {
    // Left is actually clean and the right has it → swap both.
    store.swap("rightonly");
  }
  closeZoom();
}

function attemptDecision(corner) {
  if (!current.value) return;
  const conflict = store.decisionConflict(corner);
  if (conflict) {
    // Hold: the user is about to contradict a confident prior call on this picture.
    pendingDecision.value = { corner, conflict };
    return;
  }
  dispatchDecision(corner);
}

// Inline confirm bar: apply the staged corner, or cancel and leave the card unchanged.
function confirmPendingDecision() {
  const pending = pendingDecision.value;
  pendingDecision.value = null;
  if (pending) dispatchDecision(pending.corner);
}
function cancelPendingDecision() {
  pendingDecision.value = null;
}

// The four corners of "which really has the tag". Left is always the flagged image.
function markBoth() {
  attemptDecision("both");
}
function markNeither() {
  attemptDecision("neither");
}
function markLeftOnly() {
  attemptDecision("leftonly");
}
function markRightOnly() {
  attemptDecision("rightonly");
}

// Copy for the confirm bar: which way the prior call went and how many times.
const CORNER_LABELS_FULL = {
  leftonly: "Left only",
  both: "Both",
  neither: "Neither",
  rightonly: "Right only ⇄",
};
const pendingMessage = computed(() => {
  const pending = pendingDecision.value;
  if (!pending) return "";
  const { conflict } = pending;
  const tag = current.value?.tag ?? "this";
  // The conflict is on the OPPOSITE of what we're about to assert: if we're now asserting
  // "has", the prior confident calls were "clean", and vice-versa.
  const priorClean = conflict.asserting === "has";
  const count = priorClean ? conflict.priorNot : conflict.priorHas;
  const priorPhrase = priorClean ? "clean" : `having “${tag}”`;
  const label = CORNER_LABELS_FULL[pending.corner] || pending.corner;
  return `You've already marked #${conflict.pid} as ${priorPhrase} ${count}× this session. Apply “${label}” anyway?`;
});

// Subtle per-pane consistency hint: summarise prior votes for a picture under the active
// tag. Empty string when there is nothing prior, so the chip only renders when it matters.
function voteHint(pid) {
  const { has, not } = store.votesForPicture(pid);
  if (!has && !not) return "";
  if (has && not) return `${not}× clean · ${has}× has it`;
  if (has) return `you've said it has it ${has}×`;
  return `you've called this clean ${not}×`;
}

function imgSrc(id, ext) {
  if (!props.backendUrl || !id) return "";
  if (ext) return `${props.backendUrl}/pictures/${id}.${ext}`;
  // Fallback when the extension is unknown: the 384px thumbnail.
  return `${props.backendUrl}/pictures/thumbnails/${id}.webp`;
}

function onImgError(event, id) {
  // Full-res failed (e.g. unusual extension) — fall back to the thumbnail once.
  const el = event?.target;
  if (!el || el.dataset.fellBack) return;
  el.dataset.fellBack = "1";
  el.src = `${props.backendUrl}/pictures/thumbnails/${id}.webp`;
}

// --- Zoom: click an image to inspect it full-screen; scroll/buttons magnify, drag-scroll pans.
const zoom = ref(null); // { src } or null
const zoomScale = ref(1);
const zoomNaturalW = ref(0);

// The hint pill sits at bottom-centre and would otherwise permanently cover that
// part of the image (native scroll can't pan content out from under a fixed pill).
// So fade it out when idle and bring it back on any zoom/pan/mouse activity — it
// re-appears showing the live percentage exactly when the user is interacting.
const hintVisible = ref(true);
let hintTimer = null;
function pokeHint() {
  hintVisible.value = true;
  if (hintTimer) clearTimeout(hintTimer);
  hintTimer = setTimeout(() => {
    hintVisible.value = false;
  }, 2200);
}
function clearHintTimer() {
  if (hintTimer) {
    clearTimeout(hintTimer);
    hintTimer = null;
  }
}

const zoomStyle = computed(() =>
  zoomNaturalW.value
    ? { width: `${Math.round(zoomNaturalW.value * zoomScale.value)}px` }
    : {},
);

function openZoom(id, ext) {
  zoom.value = { src: imgSrc(id, ext) };
  zoomScale.value = 1;
  zoomNaturalW.value = 0;
  pokeHint();
}
function closeZoom() {
  zoom.value = null;
  clearHintTimer();
}
function onZoomLoad(event) {
  // Start at "fit the viewport height" so detail is already visible, then let the
  // user magnify further. naturalWidth drives the pixel width we render at.
  const img = event.target;
  zoomNaturalW.value = img.naturalWidth || 0;
  const fitByHeight =
    img.naturalHeight > 0 ? (window.innerHeight * 0.92) / img.naturalHeight : 1;
  zoomScale.value = Math.max(0.25, Math.min(fitByHeight, 4));
}
function nudgeZoom(factor) {
  zoomScale.value = Math.max(0.1, Math.min(zoomScale.value * factor, 12));
}
function onZoomWheel(event) {
  nudgeZoom(event.deltaY < 0 ? 1.15 : 1 / 1.15);
  pokeHint();
}

// Drag-to-pan vs click-to-close: track how far the pointer moved between
// mousedown and the click. A real drag pans the scroll and suppresses the close.
const panning = ref(false);
let panLastX = 0;
let panLastY = 0;
let panDist = 0;

function onZoomMouseDown(event) {
  if (event.button !== 0) return;
  panning.value = true;
  panDist = 0;
  panLastX = event.clientX;
  panLastY = event.clientY;
  event.preventDefault(); // suppress native image drag / text selection
}
function onZoomMouseMove(event) {
  pokeHint();
  if (!panning.value) return;
  const dx = event.clientX - panLastX;
  const dy = event.clientY - panLastY;
  panLastX = event.clientX;
  panLastY = event.clientY;
  panDist += Math.abs(dx) + Math.abs(dy);
  const el = event.currentTarget;
  el.scrollLeft -= dx;
  el.scrollTop -= dy;
}
function onZoomMouseUp() {
  panning.value = false;
}
function onZoomClick() {
  // Treat a movement of more than a few px as a pan, not a close.
  if (panDist > 6) {
    panDist = 0;
    return;
  }
  closeZoom();
}

// Skip: rotate the current item to the back without recording a decision.
function skip() {
  if (store.items.length > 1) {
    const [head, ...rest] = store.items;
    store.items = [...rest, head];
  }
}

// Is the user actively typing into a genuine text-entry field? Only then should the overlay
// hand the keystroke through and skip its decision shortcuts. Crucially this NO LONGER
// includes <select>: a native <select> retains focus after a scope change, and treating it
// as editable made handleKeyDown bail, so a decision key (L/B/N/R) fell through to the
// <select>'s native type-ahead and jumped to a set/character starting with that letter.
// A <select> is a fixed-choice control, not text entry — the decision keys must win over it.
// Text entry that DOES bail: <input> (the tag-search autocomplete is an <input>; typing
// there filters and must pass through), <textarea>, and contenteditable.
function isEditable(el) {
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA";
}

function handleKeyDown(event) {
  // Let typing in the tag picker / any input through untouched.
  if (isEditable(event.target) || isEditable(document.activeElement)) return;

  // Left/Right select a pane (Shift/Ctrl add the other). Handled before the
  // modifier guard below so Shift/Ctrl-arrow can extend the selection.
  if (
    current.value &&
    !pendingDecision.value &&
    !zoom.value &&
    (event.key === "ArrowLeft" || event.key === "ArrowRight")
  ) {
    selectPaneByArrow(
      event.key === "ArrowLeft" ? "left" : "right",
      event.shiftKey || event.ctrlKey || event.metaKey,
    );
    event.preventDefault();
    event.stopImmediatePropagation();
    return;
  }

  if (event.metaKey || event.ctrlKey || event.altKey) return;

  const key = event.key.toLowerCase();

  // A pending consistency confirm takes priority over everything else: Enter applies the
  // staged corner, Escape cancels it (and must NOT fall through to zoom/preview/close).
  // The L/B/N/R decision keys are swallowed until it's resolved.
  if (pendingDecision.value) {
    if (key === "enter") {
      confirmPendingDecision();
    } else if (key === "escape") {
      cancelPendingDecision();
    } else if (["l", "b", "n", "r"].includes(key)) {
      // swallow — don't let a new decision fire while one is awaiting confirmation
    } else {
      return; // leave any other key (it's harmless) untouched
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    return;
  }

  // Escape unwinds the topmost layer: tag menu → zoom → pane selection → overlay.
  // A pane selection is cleared before the overlay closes, so the first Esc drops
  // the selection and the second leaves.
  if (key === "escape") {
    if (tagApplyOpen.value) closeTagApply();
    else if (zoom.value) closeZoom();
    else if (selectedPaneIds.value.length) selectedPaneIds.value = [];
    else emit("close");
    event.preventDefault();
    event.stopImmediatePropagation();
    return;
  }

  let handled = true;
  if (key === "l") {
    markLeftOnly();
  } else if (key === "b") {
    markBoth();
  } else if (key === "n") {
    markNeither();
  } else if (key === "r") {
    markRightOnly();
  } else if (key === "s") {
    skip();
    closeZoom();
  } else if (key === "u") {
    store.undo();
    closeZoom();
  } else if (key === "t") {
    openTagApply();
  } else {
    handled = false;
  }
  if (handled) {
    // Capture-phase + stopImmediatePropagation keeps these keys from also
    // reaching the grid's nav handler and App's global shortcuts behind us.
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}

onMounted(() => {
  window.addEventListener("keydown", handleKeyDown, true);
  store.load(initialScopeFromSelection());
});

onUnmounted(() => {
  window.removeEventListener("keydown", handleKeyDown, true);
  // Belt-and-braces: drop the tag-menu's global listeners if the overlay unmounts while open.
  document.removeEventListener("pointerdown", handleTagOutsideClick, true);
  window.removeEventListener("resize", positionTagMenu);
  clearHintTimer();
});
</script>

<style scoped>
.rf-overlay {
  position: fixed;
  /* Anchor below the desktop title bar (0px in a browser) so the bar and its
     window controls stay visible and the shell centres within the area below. */
  inset: var(--titlebar-h) 0 0 0;
  z-index: 4000;
  background: rgba(var(--v-theme-scrim), 0.82);
  display: flex;
  align-items: stretch;
  justify-content: center;
}

.rf-shell {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 1400px;
  margin: 0 auto;
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
}

/* The topbar chrome (bar surface, Close button, title) now lives in the shared
   OverlayToolbar widget. The rules below only style the review-specific controls
   slotted into #actions, matched to the toolbar's control language: 32px-tall
   surfaces, 4px radius, the same dark-surface theme tokens as .overlay-icon-btn. */

/* Compact the shared toolbar chrome for THIS overlay only. The review bar packs a tag
   picker, three scope dropdowns, a penalised toggle and a progress tally into one row, so
   the default title/close type scale and 12px gaps pushed it to three rows on a narrow
   window. Scoped under .rf-shell via :deep so ImageOverlay's toolbar keeps its own scale. */
.rf-shell :deep(.overlay-toolbar) {
  gap: var(--space-3);
  min-height: 36px;
  padding: var(--space-2) var(--space-3);
}
.rf-shell :deep(.overlay-toolbar-actions) {
  gap: var(--space-2);
}
/* Title sits next to Close and does NOT grow (the shell default is flex:1, which
   would shove the tag/scope groups to the right edge); a .rf-spacer pushes the
   progress block right instead, matching the design. */
.rf-shell :deep(.overlay-toolbar-title) {
  flex: 0 0 auto;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.01em;
  white-space: nowrap;
}
.rf-shell :deep(.overlay-toolbar-close) {
  font-size: var(--text-2xs);
  padding: var(--space-2) var(--space-3);
}

.rf-field {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
.rf-field-label {
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

/* Vertical hairline between toolbar groups (title | tag | scope). */
.rf-divider {
  flex: 0 0 auto;
  width: 1px;
  height: 24px;
  background: rgba(var(--v-theme-on-dark-surface), 0.18);
}
/* Flexible spacer: separates the left cluster (title + tag controls) from the
   right cluster (scope filters + progress). */
.rf-spacer {
  flex: 1 1 auto;
  min-width: var(--space-3);
}
/* Penalised-only filter as a compact toggle button (not a checkbox + label):
   neutral pill when off, error-tinted when on. */
.rf-penalised-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 32px;
  padding: 0 var(--space-3);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  cursor: pointer;
  transition:
    background 0.12s,
    border-color 0.12s,
    color 0.12s;
}
.rf-penalised-toggle:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.14);
}
.rf-penalised-toggle--on {
  border-color: rgb(var(--v-theme-error));
  background: color-mix(in srgb, rgb(var(--v-theme-error)) 15%, transparent);
  color: rgb(var(--v-theme-error));
}
/* Shared neutral dark control. The neutral buttons/selects in this overlay
   (scope selects, tag trigger, retry, confirm, twin-fix) all wear the same
   dark-surface skin: faint fill, low-contrast border, sm radius, lift on hover,
   fade when disabled. Define it once here; each control keeps only its own
   size/padding/width overrides in its own rule. Semantic-coloured buttons
   (.rf-action--*, .rf-confirm-btn--apply) deliberately do NOT use this. */
.rf-control,
.rf-twin-fix {
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.rf-control:hover:not(:disabled),
.rf-twin-fix:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.14);
}
.rf-control:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.rf-select {
  height: 32px;
  padding: 0 var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  max-width: 280px;
}
/* Scope filters sit in the same row as the tag picker; keep them compact so the row
   stays tidy and wraps cleanly when narrow. Native <select> clips the value to this width
   and still shows the full option text in the open list. */
.rf-select--scope {
  max-width: 118px;
}

.rf-scan-error {
  color: rgb(var(--v-theme-error));
  font-size: var(--text-2xs);
}

/* Custom tag picker, matched to the toolbar control language: the trigger is the same
   surface/height/radius as .rf-select, and the floating panel (position:fixed from the
   trigger's rect — see tagMenuStyle) reads as the same dark family. */
.rf-field--tag {
  gap: var(--space-2);
}
.rf-tag-pick {
  position: relative;
  width: 150px;
  font-size: var(--text-sm);
}
.rf-tag-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  width: 100%;
  height: 32px;
  padding: 0 var(--space-3);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}
.rf-tag-trigger-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rf-tag-trigger-label--placeholder {
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
}
.rf-tag-chevron {
  flex: none;
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
}

/* Floating picker panel (position:fixed via tagMenuStyle so it escapes the bar clip). */
.rf-tag-menu {
  position: fixed;
  z-index: 4100;
  width: 240px;
  max-height: 320px;
  display: flex;
  flex-direction: column;
  background: rgb(var(--v-theme-dark-surface));
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: var(--radius-sm);
  box-shadow: var(--elevation-4);
  overflow: hidden;
}
.rf-tag-search {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
}
.rf-tag-search input {
  flex: 1;
  min-width: 0;
  background: none;
  border: none;
  outline: none;
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-xs);
}
.rf-tag-search input::placeholder {
  color: rgba(var(--v-theme-on-dark-surface), 0.5);
}
.rf-tag-list {
  overflow-y: auto;
  padding: var(--space-2) 0;
}
.rf-tag-empty {
  padding: var(--space-3) var(--space-4);
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  font-size: var(--text-xs);
}
.rf-tag-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-2) var(--space-4);
  background: none;
  border: none;
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-xs);
  text-align: left;
  cursor: pointer;
}
.rf-tag-item--active {
  background: rgba(var(--v-theme-primary), 0.25);
}

/* "Rescan current tag" icon-button. It carries .overlay-icon-btn (the shared toolbar
   look) and this only constrains it to a 32px square so it sits flush beside the picker. */
.rf-rescan {
  width: 32px;
  padding: 0;
}

/* Anomaly (smart-score-penalised) tags stand out red in both the list and the selection. */
.rf-tag-anomaly {
  color: rgb(var(--v-theme-error));
  font-weight: var(--weight-semibold);
}
.rf-tag-count {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  font-size: var(--text-2xs);
  margin-left: var(--space-2);
}
.rf-tag-option {
  font-size: var(--text-xs);
}

/* Progress: a single right-aligned row — "{n} left" then the colour-coded tally. */
.rf-progress {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}
.rf-progress-remaining {
  font-weight: var(--weight-semibold);
  font-size: var(--text-sm);
}
.rf-progress-tally {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}
.rf-tally {
  font-weight: var(--weight-semibold);
}
.rf-tally--removed {
  color: rgb(var(--v-theme-error));
}
.rf-tally--added {
  color: rgb(var(--v-theme-primary));
}
.rf-tally--kept {
  color: rgb(var(--v-theme-success));
}

.rf-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: var(--space-4) var(--space-5) var(--space-5);
}

.rf-state {
  margin: auto;
  text-align: center;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
}
.rf-state--error {
  color: rgb(var(--v-theme-error));
}
.rf-state--done {
  color: rgb(var(--v-theme-success));
}
.rf-state-sub {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  font-size: var(--text-xs);
}
.rf-retry {
  padding: var(--space-2) var(--space-4);
}

.rf-review {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
}

/* Direct-tag affordance, pinned bottom-right of the review view. */
.rf-tag-apply {
  position: absolute;
  right: 0;
  bottom: 0;
  z-index: 20;
}
.rf-tag-apply-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgba(var(--v-theme-primary), 0.55);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-primary), 0.16);
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  cursor: pointer;
  box-shadow: var(--elevation-3);
}
.rf-tag-apply-btn:hover,
.rf-tag-apply-btn--open {
  background: rgba(var(--v-theme-primary), 0.28);
  border-color: rgba(var(--v-theme-primary), 0.8);
}
.rf-tag-apply-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
}
/* The tag menu opens above the button. */
.rf-tag-apply-panel {
  position: absolute;
  right: 0;
  bottom: calc(100% + 8px);
  width: 320px;
  max-width: 80vw;
}
.rf-verdict {
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: var(--space-4);
  font-size: var(--text-lg);
  font-weight: var(--weight-bold);
  text-align: center;
}
.rf-verdict-pct {
  font-size: var(--text-xl);
  font-weight: var(--weight-bold);
  padding: var(--space-1) var(--space-4);
  border-radius: var(--radius-md);
}
.rf-verdict-text {
  color: rgb(var(--v-theme-on-dark-surface));
}
.rf-verdict--remove .rf-verdict-pct {
  background: rgba(var(--v-theme-error), 0.18);
  color: rgb(var(--v-theme-error));
}
.rf-verdict--add .rf-verdict-pct {
  background: rgba(var(--v-theme-success), 0.18);
  color: rgb(var(--v-theme-success));
}
.rf-signals {
  display: flex;
  gap: var(--space-3);
  justify-content: center;
  flex-wrap: wrap;
}
.rf-signal-chip {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: var(--radius-pill);
  padding: var(--space-2) var(--space-3);
}
.rf-signal-chip--muted {
  color: rgba(var(--v-theme-on-dark-surface), 0.5);
  font-weight: var(--weight-regular);
}
.rf-verdict-sub {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  font-size: var(--text-xs);
  text-align: center;
  margin: 0;
}
/* Combined prompt + guidance banner: merges the verdict bucket and the review
   question into one strip. The bucket's accent (set per-corner below) colours the
   icon, the 4px left border, a tint of the fill, and the title pill; with no
   tagger prediction the neutral variant leaves the accent var unset. */
.rf-banner {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-4);
  width: 100%;
  max-width: 1180px;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid
    var(--rf-banner-accent, rgba(var(--v-theme-on-dark-surface), 0.18));
  border-left: 4px solid
    var(--rf-banner-accent, rgba(var(--v-theme-on-dark-surface), 0.4));
  background: color-mix(
    in srgb,
    var(--rf-banner-accent, rgb(var(--v-theme-on-dark-surface))) 12%,
    rgb(var(--v-theme-dark-surface))
  );
}
.rf-banner-icon {
  color: var(--rf-banner-accent, currentColor);
  flex-shrink: 0;
}
.rf-banner-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: left;
}
.rf-banner-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rf-banner-meaning {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.72);
}
.rf-banner-aside {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--space-1);
}
.rf-banner-pill {
  font-size: var(--text-2xs);
  font-weight: var(--weight-bold);
  padding: 2px var(--space-3);
  border-radius: var(--radius-pill);
  white-space: nowrap;
  color: var(--rf-banner-accent);
  background: color-mix(in srgb, var(--rf-banner-accent) 22%, transparent);
}
.rf-banner-count {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.rf-banner--neither {
  --rf-banner-accent: rgb(var(--v-theme-error));
}
.rf-banner--both {
  --rf-banner-accent: rgb(var(--v-theme-primary));
}
.rf-banner--leftonly {
  --rf-banner-accent: rgb(var(--v-theme-accent));
}
.rf-banner--rightonly {
  --rf-banner-accent: rgb(var(--v-theme-tertiary));
}

.rf-pair {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: var(--space-5);
  width: 100%;
  justify-content: center;
  align-items: stretch;
}
/* Each pane is a bordered card: a header strip (id + prediction + confidence)
   over an image that fills the rest, with an optional vote-hint foot. The flagged
   (left) pane carries the accent border. */
.rf-pane {
  margin: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1 1 0;
  max-width: 50%;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: rgba(var(--v-theme-on-dark-surface), 0.04);
}
.rf-pane--flagged {
  border-color: rgb(var(--v-theme-accent));
}
.rf-pane--selected {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -1px;
}
.rf-pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
}
.rf-pane-id {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, monospace);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rf-pane-pred {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  font-size: var(--text-2xs);
}
.rf-pane-pred-label {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  color: rgb(var(--v-theme-on-dark-surface));
}
.rf-pane-pred-label--has {
  color: rgb(var(--v-theme-accent));
}
/* Image area wraps the <img> so the magnifier button and selection check can
   be positioned over it. Takes the flex space the <img> used to. */
.rf-img-wrap {
  position: relative;
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  background: rgb(var(--v-theme-dark-surface));
}
.rf-img {
  flex: 1;
  min-height: 0;
  min-width: 0;
  max-width: 100%;
  object-fit: contain;
  object-position: center;
  background: rgb(var(--v-theme-dark-surface));
  /* Click selects now; the magnifier button / scroll wheel zoom. */
  cursor: pointer;
}
.rf-zoom-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-scrim), 0.66);
  color: rgb(var(--v-theme-on-dark-surface));
  cursor: zoom-in;
  opacity: 0;
  transition: opacity var(--dur-1) var(--ease-standard);
}
.rf-img-wrap:hover .rf-zoom-btn {
  opacity: 1;
}
.rf-zoom-btn:hover {
  background: rgba(var(--v-theme-scrim), 0.9);
}
/* Selection check, shown only when the pane is selected. */
.rf-pane-check {
  position: absolute;
  top: 6px;
  left: 6px;
  width: 24px;
  height: 24px;
  display: none;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}
.rf-pane--selected .rf-pane-check {
  display: inline-flex;
}
.rf-pane-foot {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-2) var(--space-3);
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
  color: rgba(var(--v-theme-on-dark-surface), 0.5);
  font-size: var(--text-2xs);
}
.rf-pane-conf {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  white-space: nowrap;
}
.rf-twin-fix {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-2xs);
}
.rf-twin-fix:hover {
  color: rgb(var(--v-theme-on-dark-surface));
}
.rf-twin-fix kbd {
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
  border-radius: var(--radius-sm);
  padding: 1px var(--space-2);
}

.rf-actions {
  display: flex;
  gap: var(--space-4);
  flex-wrap: wrap;
  justify-content: center;
}
.rf-action {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-5);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  cursor: pointer;
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rf-action kbd {
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
  border-radius: var(--radius-sm);
  padding: 1px var(--space-2);
  font-size: var(--text-2xs);
}
/* Decisions read as one neutral set; only the tagger's likely call is lifted with
   an accent border + tint and a "likely" pip, so the eye lands on it without the
   whole row turning into competing colours. */
.rf-action:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-dark-surface), 0.14);
}
.rf-action--undo:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.rf-action--rec {
  border-color: rgb(var(--v-theme-accent));
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-accent)) 14%,
    rgba(var(--v-theme-on-dark-surface), 0.08)
  );
}
.rf-action--rec:hover:not(:disabled) {
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-accent)) 22%,
    rgba(var(--v-theme-on-dark-surface), 0.08)
  );
}
.rf-actions-label {
  align-self: center;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  font-size: var(--text-xs);
  margin-right: var(--space-1);
}
.rf-rec-pip {
  font-size: var(--text-2xs);
  font-weight: var(--weight-bold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgb(var(--v-theme-accent));
}
.rf-actions-gap {
  width: 32px;
  flex: 0 0 auto;
}

/* Inline consistency-confirm bar (not a modal): a compact strip under the action row,
   only present while a decision contradicts a confident earlier call this session. */
.rf-confirm {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  flex-wrap: wrap;
  justify-content: center;
  max-width: 760px;
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-warning));
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-warning), 0.14);
}
.rf-confirm-msg {
  color: rgb(var(--v-theme-warning));
  font-size: var(--text-xs);
}
.rf-confirm-actions {
  display: inline-flex;
  gap: var(--space-3);
}
.rf-confirm-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
}
.rf-confirm-btn--apply {
  background: rgb(var(--v-theme-warning));
  border-color: rgb(var(--v-theme-warning));
  color: rgb(var(--v-theme-on-warning));
}
/* Beat .rf-control:hover:not(:disabled) (equal specificity, defined earlier) so the
   semantic warning hover wins over the shared neutral hover on the Apply button. */
.rf-confirm-btn--apply:hover:not(:disabled) {
  background: rgba(var(--v-theme-warning), 0.85);
}
.rf-confirm-btn kbd {
  background: rgba(var(--v-theme-on-dark-surface), 0.14);
  border-radius: var(--radius-sm);
  padding: 1px var(--space-2);
  font-size: var(--text-2xs);
}

/* Passive per-pane consistency chip: subtle, muted, only rendered when there is a prior
   vote for that picture, to nudge consistency before a conflict even happens. */
.rf-vote-hint {
  color: rgb(var(--v-theme-warning));
  font-size: var(--text-2xs);
  font-style: italic;
}

/* Full-screen zoom layer */
.rf-zoom {
  position: fixed;
  /* Anchor below the title bar (0px in a browser) so the bar / window controls
     stay usable while zoomed. The reduced-height box is the scroll container, so
     the zoomed image pans/scrolls within the area below the bar. */
  inset: var(--titlebar-h) 0 0 0;
  z-index: 4100;
  background: rgba(var(--v-theme-scrim), 0.92);
  overflow: auto;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  cursor: grab;
  user-select: none;
}
.rf-zoom--panning {
  cursor: grabbing;
}
.rf-zoom-img {
  display: block;
  margin: auto;
  max-width: none;
  height: auto;
  cursor: inherit;
  user-select: none;
  -webkit-user-drag: none;
}
.rf-zoom-hint {
  position: fixed;
  left: 50%;
  bottom: 16px;
  transform: translateX(-50%);
  pointer-events: none;
  background: rgba(var(--v-theme-dark-surface), 0.9);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: var(--radius-pill);
  padding: var(--space-2) var(--space-4);
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
  font-size: var(--text-2xs);
  white-space: nowrap;
  transition: opacity var(--dur-3) var(--ease-standard);
}
.rf-zoom-hint--hidden {
  opacity: 0;
}
.rf-zoom-hint kbd {
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: var(--radius-sm);
  padding: 1px var(--space-2);
}
</style>
