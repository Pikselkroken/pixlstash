<template>
  <div class="rf-overlay" @click.self="emit('close')">
    <div class="rf-shell">
      <!-- Topbar: close, title, tag picker, direction filter, progress -->
      <header class="rf-topbar">
        <button
          class="rf-close"
          @click="emit('close')"
          aria-label="Close (ESC)"
          title="Close (ESC)"
        >
          <v-icon size="18">mdi-close</v-icon>
          <span>Close</span>
        </button>

        <div class="rf-title">Review suggested fixes</div>

        <label class="rf-field">
          <span class="rf-field-label">Tag</span>
          <select
            class="rf-select"
            :value="store.activeTag || ''"
            @change="store.selectTag($event.target.value)"
          >
            <option v-for="t in store.tags" :key="t.tag" :value="t.tag">
              {{ t.tag }} ({{ t.total }})
            </option>
          </select>
        </label>

        <!-- Scope filters: narrow the queue to a project / set / character. Each "Any"
             option clears that dimension; changes re-run summary/queue/bulk via setScope. -->
        <label class="rf-field">
          <span class="rf-field-label">Project</span>
          <select
            class="rf-select rf-select--scope"
            :value="store.scope.projectId ?? ''"
            @change="onScopeChange('projectId', $event.target.value)"
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
            class="rf-select rf-select--scope"
            :value="store.scope.setId ?? ''"
            @change="onScopeChange('setId', $event.target.value)"
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
            class="rf-select rf-select--scope"
            :value="store.scope.characterId ?? ''"
            @change="onScopeChange('characterId', $event.target.value)"
          >
            <option value="">Any</option>
            <option value="UNASSIGNED">Unassigned</option>
            <option v-for="c in store.characters" :key="c.id" :value="c.id">
              {{ c.name || `Character ${c.id}` }}
            </option>
          </select>
        </label>

        <button
          v-if="hasScopeFilter"
          class="rf-clear-filters"
          type="button"
          title="Clear the project / set / character filters"
          @click="clearScope"
        >
          Clear filters
        </button>

        <div class="rf-scan">
          <input
            v-model="scanInput"
            class="rf-scan-input"
            type="text"
            list="rf-tag-list"
            placeholder="tag to scan…"
            :disabled="store.scanning"
            @keydown.enter="doScan"
          />
          <datalist id="rf-tag-list">
            <option v-for="t in store.allTags" :key="t" :value="t" />
          </datalist>
          <button
            class="rf-scan-btn"
            type="button"
            :disabled="store.scanning || !scanInput.trim()"
            :title="`Re-scan “${scanInput.trim()}” for near-neighbour disagreements`"
            @click="doScan"
          >
            {{ store.scanning ? "Scanning…" : "Scan" }}
          </button>
          <span v-if="store.scanError" class="rf-scan-error">{{ store.scanError }}</span>
        </div>

        <div class="rf-progress">
          <span class="rf-progress-remaining">{{ store.remainingTotal }} left</span>
          <span class="rf-progress-tally">
            ✗ {{ store.removedCount }} · + {{ store.addedCount }} · ✓
            {{ store.keptCount }}
          </span>
        </div>
      </header>

      <!-- Bulk: auto-resolve only pairs where the near-twin vote and the tagger AGREE,
           so you hand-compare the rest. -->
      <div class="rf-bulkbar">
        <label class="rf-bulk-thresh">
          Auto-resolve when both signals agree ≥
          <select
            :value="store.bulkThreshold"
            @change="store.setBulkThreshold(Number($event.target.value))"
          >
            <option :value="0.99">99%</option>
            <option :value="0.95">95%</option>
            <option :value="0.9">90%</option>
            <option :value="0.8">80%</option>
          </select>
        </label>
        <button
          class="rf-bulk-preview"
          type="button"
          :disabled="store.bulkCount === 0"
          title="Spot-check a sample of what would be auto-resolved before applying."
          @click="store.previewOpen = true"
        >
          Preview
        </button>
        <button
          class="rf-bulk-btn"
          type="button"
          :disabled="store.bulkCount === 0 || store.bulkBusy"
          :title="`Apply every pair where the near-twin vote and the tagger agree and both clear the threshold for “${store.activeTag}”, in one go (undoable).`"
          @click="store.runBulk()"
        >
          Resolve {{ store.bulkCount }} agreed one{{ store.bulkCount === 1 ? "" : "s" }}
        </button>
        <span v-if="store.lastBulk" class="rf-bulk-undo">
          resolved {{ store.lastBulk.count }} ·
          <button type="button" :disabled="store.bulkBusy" @click="store.undoBulk()">
            Undo all
          </button>
        </span>
      </div>

      <!-- Body -->
      <section class="rf-body">
        <div v-if="store.loading" class="rf-state">Loading…</div>

        <div v-else-if="store.error" class="rf-state rf-state--error">
          {{ store.error }}
          <button class="rf-retry" @click="store.fetchQueue()">Retry</button>
        </div>

        <div v-else-if="!current" class="rf-state rf-state--done">
          <v-icon size="40">mdi-check-all</v-icon>
          <p v-if="!store.activeTag">
            No suggestions{{ hasScopeFilter ? " match the current filters" : "" }}.
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
          <div class="rf-question">
            The left is flagged “{{ current.tag }}” — but is it?
            <span class="rf-question-sub">{{ verdict.sub }}</span>
          </div>

          <div class="rf-pair">
            <figure class="rf-pane rf-pane--flagged">
              <div class="rf-pane-head">
                <span class="rf-pane-title">Left · the flagged one</span>
                <span class="rf-chip rf-chip--has">⚑ predicted “{{ current.tag }}”</span>
              </div>
              <img
                class="rf-img"
                :src="imgSrc(taggedSide.id, taggedSide.ext)"
                :alt="`picture ${taggedSide.id}`"
                title="Click to zoom"
                @click="openZoom(taggedSide.id, taggedSide.ext)"
                @error="onImgError($event, taggedSide.id)"
              />
              <figcaption class="rf-pane-foot">
                <span>#{{ taggedSide.id }}</span>
                <span class="rf-pane-conf">{{ confLabel(taggedSide.conf) }}</span>
                <span v-if="voteHint(taggedSide.id)" class="rf-vote-hint">
                  {{ voteHint(taggedSide.id) }}
                </span>
              </figcaption>
            </figure>

            <figure class="rf-pane">
              <div class="rf-pane-head">
                <span class="rf-pane-title">Right · its near-twin</span>
                <span class="rf-chip rf-chip--missing">not flagged</span>
              </div>
              <img
                class="rf-img"
                :src="imgSrc(untaggedSide.id, untaggedSide.ext)"
                :alt="`picture ${untaggedSide.id}`"
                title="Click to zoom"
                @click="openZoom(untaggedSide.id, untaggedSide.ext)"
                @error="onImgError($event, untaggedSide.id)"
              />
              <figcaption class="rf-pane-foot">
                <span>#{{ untaggedSide.id }}</span>
                <span class="rf-pane-conf">{{ confLabel(untaggedSide.conf) }}</span>
                <span v-if="voteHint(untaggedSide.id)" class="rf-vote-hint">
                  {{ voteHint(untaggedSide.id) }}
                </span>
              </figcaption>
            </figure>
          </div>

          <div class="rf-actions">
            <span class="rf-actions-label">Which really has “{{ current.tag }}”?</span>
            <button
              class="rf-action rf-action--leftonly"
              :class="{ 'rf-action--rec': verdict.strong && verdict.corner === 'leftonly' }"
              type="button"
              title="The flag is right and the twin is clean — only the left has it. No change."
              @click="markLeftOnly"
            >
              <kbd>L</kbd> Left only
              <span v-if="verdict.strong && verdict.corner === 'leftonly'" class="rf-rec-pip">likely</span>
            </button>
            <button
              class="rf-action rf-action--both"
              :class="{ 'rf-action--rec': verdict.strong && verdict.corner === 'both' }"
              type="button"
              title="Both have it — tag the right one too."
              @click="markBoth"
            >
              <kbd>B</kbd> Both
              <span v-if="verdict.strong && verdict.corner === 'both'" class="rf-rec-pip">likely</span>
            </button>
            <button
              class="rf-action rf-action--neither"
              :class="{ 'rf-action--rec': verdict.strong && verdict.corner === 'neither' }"
              type="button"
              title="Neither has it — the flag was wrong, clear the left."
              @click="markNeither"
            >
              <kbd>N</kbd> Neither
              <span v-if="verdict.strong && verdict.corner === 'neither'" class="rf-rec-pip">likely</span>
            </button>
            <button
              class="rf-action rf-action--swap"
              :class="{ 'rf-action--rec': verdict.strong && verdict.corner === 'rightonly' }"
              type="button"
              title="The left is actually clean and the RIGHT has it — swap: clear the left, tag the right."
              @click="markRightOnly"
            >
              <kbd>R</kbd> Right only ⇄
              <span v-if="verdict.strong && verdict.corner === 'rightonly'" class="rf-rec-pip">likely</span>
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
                class="rf-confirm-btn rf-confirm-btn--apply"
                type="button"
                title="Apply this decision despite the earlier call (Enter)."
                @click="confirmPendingDecision"
              >
                <kbd>↵</kbd> Apply
              </button>
              <button
                class="rf-confirm-btn"
                type="button"
                title="Leave the card unchanged (Esc)."
                @click="cancelPendingDecision"
              >
                <kbd>Esc</kbd> Cancel
              </button>
            </div>
          </div>

          <p class="rf-hint">
            <kbd>L</kbd> left only · <kbd>B</kbd> both · <kbd>N</kbd> neither ·
            <kbd>R</kbd> right only (swap) · <kbd>S</kbd> skip · <kbd>U</kbd> undo ·
            <kbd>Esc</kbd> close
          </p>
        </div>
      </section>
    </div>

    <!-- Bulk preview: spot-check a sample of what "Resolve" would auto-apply. -->
    <div
      v-if="store.previewOpen"
      class="rf-preview"
      @click.self="store.previewOpen = false"
    >
      <div class="rf-preview-card">
        <div class="rf-preview-head">
          <div>
            Preview — {{ store.bulkCount }} agreed one{{ store.bulkCount === 1 ? "" : "s" }}
            at ≥{{ Math.round(store.bulkThreshold * 100) }}%
            <span class="rf-preview-sub">
              showing the {{ store.bulkSample.length }} least tagger-confident (the riskiest)
            </span>
          </div>
          <button
            class="rf-preview-x"
            type="button"
            @click="store.previewOpen = false"
          >
            <v-icon size="18">mdi-close</v-icon>
          </button>
        </div>

        <div v-if="!store.bulkSample.length" class="rf-preview-empty">
          No pair where both signals agree this strongly — lower the bar, or review manually.
        </div>
        <div v-else class="rf-preview-grid">
          <figure
            v-for="s in store.bulkSample"
            :key="s.id"
            class="rf-preview-item"
          >
            <div class="rf-preview-imgs">
              <img
                class="rf-preview-img rf-preview-img--flagged"
                :src="imgSrc(sampleLeft(s).id, sampleLeft(s).ext)"
                :alt="`flagged #${sampleLeft(s).id}`"
                title="Click to zoom"
                @click="openZoom(sampleLeft(s).id, sampleLeft(s).ext)"
                @error="onImgError($event, sampleLeft(s).id)"
              />
              <img
                class="rf-preview-img"
                :src="imgSrc(sampleRight(s).id, sampleRight(s).ext)"
                :alt="`twin #${sampleRight(s).id}`"
                title="Click to zoom"
                @click="openZoom(sampleRight(s).id, sampleRight(s).ext)"
                @error="onImgError($event, sampleRight(s).id)"
              />
            </div>
            <figcaption class="rf-preview-verdict">
              → {{ cornerLabel(s.corner) }} · {{ Math.round(s.confidence * 100) }}%
            </figcaption>
          </figure>
        </div>

        <div class="rf-preview-foot">
          <span class="rf-preview-note">
            Left = flagged · right = its twin. These look right? Resolve them.
          </span>
          <button
            class="rf-action rf-action--skip"
            type="button"
            @click="store.previewOpen = false"
          >
            Close
          </button>
          <button
            class="rf-bulk-btn"
            type="button"
            :disabled="store.bulkCount === 0 || store.bulkBusy"
            @click="store.runBulk()"
          >
            Resolve all {{ store.bulkCount }}
          </button>
        </div>
      </div>
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
      <div class="rf-zoom-hint" :class="{ 'rf-zoom-hint--hidden': !hintVisible }">
        {{ Math.round(zoomScale * 100) }}% · scroll to zoom · drag to pan · click or Esc
        to close · <kbd>←</kbd>/<kbd>→</kbd> still apply
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
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
const emit = defineEmits(["close"]);

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
function onScopeChange(dimension, raw) {
  let value = null;
  if (raw !== "" && raw != null) {
    if (dimension === "characterId") {
      value = raw === "UNASSIGNED" ? "UNASSIGNED" : Number(raw);
    } else {
      value = Number(raw);
    }
  }
  store.setScope({ [dimension]: value });
}

function clearScope() {
  store.setScope({ projectId: null, setId: null, characterId: null });
}

// "Scan a tag" control — prefilled with the active tag (re-scan), editable for a new one.
const scanInput = ref("");
watch(
  () => store.activeTag,
  (t) => {
    if (t && !scanInput.value) scanInput.value = t;
  },
  { immediate: true },
);
function doScan() {
  store.scanTag(scanInput.value);
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
  return { id: i.twin_picture_id, ext: i.twin_ext, conf: i.twin_tagger_confidence };
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

const CORNER_LABELS = {
  both: "Both",
  neither: "Neither",
  leftonly: "Left only",
  rightonly: "Right only ⇄",
};
function cornerLabel(c) {
  return CORNER_LABELS[c] || c;
}
// In a bulk-sample item, left = the flagged image, right = its twin.
function sampleLeft(s) {
  return s.direction === "remove"
    ? { id: s.picture_id, ext: s.picture_ext }
    : { id: s.twin_picture_id, ext: s.twin_ext };
}
function sampleRight(s) {
  return s.direction === "remove"
    ? { id: s.twin_picture_id, ext: s.twin_ext }
    : { id: s.picture_id, ext: s.picture_ext };
}

function confLabel(conf) {
  if (conf === null || conf === undefined) return "no tagger prediction";
  const tag = current.value?.tag ?? "this";
  const pct = Math.round(conf * 100);
  // The raw confidence is P(tag applies); state which way that points so it's
  // unambiguous (high = the model thinks it IS the tag, even if it's untagged).
  return conf >= 0.5
    ? `tagger: ${pct}% sure it IS “${tag}”`
    : `tagger: ${100 - pct}% sure it's NOT “${tag}”`;
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

function isEditable(el) {
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

function handleKeyDown(event) {
  // Let typing in the tag picker / any input through untouched.
  if (isEditable(event.target) || isEditable(document.activeElement)) return;
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

  // Escape unwinds the topmost layer: zoom → preview → overlay.
  if (key === "escape") {
    if (zoom.value) closeZoom();
    else if (store.previewOpen) store.previewOpen = false;
    else emit("close");
    event.preventDefault();
    event.stopImmediatePropagation();
    return;
  }

  // The preview modal is a read-only spot-check — don't let review keys resolve the
  // queue underneath it (clicking an image to zoom still works). A zoomed preview
  // image is fine to keep open; only Esc (above) dismisses it.
  if (store.previewOpen) return;

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
  background: rgba(0, 0, 0, 0.82);
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
  background: #15161a;
  color: #e8eaed;
}

.rf-topbar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 16px;
  border-bottom: 1px solid #2a2c33;
  flex-wrap: wrap;
}

.rf-close {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #23252c;
  color: #e8eaed;
  border: 1px solid #34373f;
  border-radius: 6px;
  padding: 6px 12px;
  cursor: pointer;
}
.rf-close:hover {
  background: #2c2f37;
}

.rf-title {
  font-weight: 600;
  font-size: 1.05rem;
}

.rf-field {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.rf-field-label {
  color: #9aa0a6;
  font-size: 0.85rem;
}
.rf-select {
  background: #23252c;
  color: #e8eaed;
  border: 1px solid #34373f;
  border-radius: 6px;
  padding: 6px 10px;
  max-width: 280px;
}
/* Scope filters sit in the same row as the tag picker; keep them compact so the row
   stays tidy and wraps cleanly when narrow. */
.rf-select--scope {
  max-width: 150px;
  font-size: 0.85rem;
}

.rf-clear-filters {
  background: #23252c;
  color: #cdd0d6;
  border: 1px solid #34373f;
  border-radius: 6px;
  padding: 6px 12px;
  cursor: pointer;
  font-size: 0.85rem;
}
.rf-clear-filters:hover {
  background: #2c2f37;
  color: #e8eaed;
}

.rf-direction {
  display: inline-flex;
  border: 1px solid #34373f;
  border-radius: 6px;
  overflow: hidden;
}
.rf-dir-btn {
  background: #23252c;
  color: #cdd0d6;
  border: none;
  padding: 6px 12px;
  cursor: pointer;
  font-size: 0.85rem;
}
.rf-dir-btn--active {
  background: #3b82f6;
  color: #fff;
}

.rf-scan {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.rf-scan-input {
  background: #23252c;
  color: #e8eaed;
  border: 1px solid #34373f;
  border-radius: 6px;
  padding: 6px 10px;
  width: 150px;
  font-size: 0.85rem;
}
.rf-scan-btn {
  background: #23252c;
  color: #e8eaed;
  border: 1px solid #34373f;
  border-radius: 6px;
  padding: 6px 12px;
  cursor: pointer;
  font-size: 0.85rem;
}
.rf-scan-btn:hover:not(:disabled) {
  background: #2c2f37;
}
.rf-scan-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.rf-scan-error {
  color: #f28b82;
  font-size: 0.8rem;
}

.rf-progress {
  margin-left: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}
.rf-progress-remaining {
  font-weight: 600;
}
.rf-progress-tally {
  color: #9aa0a6;
  font-size: 0.8rem;
}

.rf-bulkbar {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 8px 16px;
  border-bottom: 1px solid #2a2c33;
  background: #17181d;
  font-size: 0.85rem;
  color: #cdd0d6;
}
.rf-bulk-thresh select {
  background: #23252c;
  color: #e8eaed;
  border: 1px solid #34373f;
  border-radius: 6px;
  padding: 3px 8px;
  margin-left: 6px;
}
.rf-bulk-btn {
  background: #1e7d44;
  color: #fff;
  border: 1px solid #1e7d44;
  border-radius: 6px;
  padding: 5px 14px;
  font-weight: 600;
  cursor: pointer;
}
.rf-bulk-btn:hover:not(:disabled) {
  background: #258a4d;
}
.rf-bulk-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.rf-bulk-undo {
  margin-left: auto;
  color: #9aa0a6;
}
.rf-bulk-undo button {
  background: #23252c;
  color: #e8eaed;
  border: 1px solid #34373f;
  border-radius: 6px;
  padding: 3px 10px;
  cursor: pointer;
}
.rf-bulk-undo button:hover {
  background: #2c2f37;
}

.rf-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 14px 16px 18px;
}

.rf-state {
  margin: auto;
  text-align: center;
  color: #9aa0a6;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.rf-state--error {
  color: #f28b82;
}
.rf-state--done {
  color: #81c995;
}
.rf-state-sub {
  color: #9aa0a6;
  font-size: 0.9rem;
}
.rf-retry {
  background: #23252c;
  color: #e8eaed;
  border: 1px solid #34373f;
  border-radius: 6px;
  padding: 6px 14px;
  cursor: pointer;
}

.rf-review {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.rf-verdict {
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 12px;
  font-size: 1.6rem;
  font-weight: 700;
  text-align: center;
}
.rf-verdict-pct {
  font-size: 2rem;
  font-weight: 800;
  padding: 2px 12px;
  border-radius: 10px;
}
.rf-verdict-text {
  color: #e8eaed;
}
.rf-verdict--remove .rf-verdict-pct {
  background: rgba(248, 113, 113, 0.18);
  color: #f8a39a;
}
.rf-verdict--add .rf-verdict-pct {
  background: rgba(129, 201, 149, 0.18);
  color: #9ad6ab;
}
.rf-signals {
  display: flex;
  gap: 8px;
  justify-content: center;
  flex-wrap: wrap;
}
.rf-signal-chip {
  font-size: 0.8rem;
  font-weight: 600;
  color: #cdd0d6;
  background: #23252c;
  border: 1px solid #34373f;
  border-radius: 999px;
  padding: 3px 10px;
}
.rf-signal-chip--muted {
  color: #80868b;
  font-weight: 400;
}
.rf-verdict-sub {
  color: #9aa0a6;
  font-size: 0.9rem;
  text-align: center;
  margin: 0;
}
.rf-question {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  font-size: 1.5rem;
  font-weight: 700;
  text-align: center;
}
.rf-question-sub {
  font-size: 0.9rem;
  font-weight: 400;
  color: #9aa0a6;
}

.rf-pair {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 18px;
  width: 100%;
  justify-content: center;
  align-items: stretch;
}
.rf-pane {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  flex: 1 1 0;
  max-width: 50%;
}
.rf-pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.rf-pane-title {
  font-weight: 600;
}
.rf-chip {
  font-size: 0.78rem;
  padding: 2px 8px;
  border-radius: 999px;
  white-space: nowrap;
}
.rf-chip--has {
  background: rgba(248, 113, 113, 0.18);
  color: #f8a39a;
}
.rf-chip--missing {
  background: rgba(129, 201, 149, 0.16);
  color: #9ad6ab;
}
.rf-img {
  flex: 1;
  min-height: 0;
  max-width: 100%;
  object-fit: contain;
  object-position: center;
  border-radius: 8px;
  background: #0c0d10;
  cursor: zoom-in;
}
.rf-pane-foot {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #80868b;
  font-size: 0.78rem;
}
.rf-pane-conf {
  color: #9aa0a6;
}
.rf-twin-fix {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #23252c;
  color: #cdd0d6;
  border: 1px solid #34373f;
  border-radius: 6px;
  padding: 3px 10px;
  cursor: pointer;
  font-size: 0.78rem;
}
.rf-twin-fix:hover {
  background: #2c2f37;
  color: #e8eaed;
}
.rf-twin-fix kbd {
  background: rgba(255, 255, 255, 0.12);
  border-radius: 4px;
  padding: 1px 5px;
}

.rf-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
}
.rf-action {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #34373f;
  border-radius: 8px;
  padding: 10px 18px;
  font-size: 0.95rem;
  font-weight: 600;
  cursor: pointer;
  background: #23252c;
  color: #e8eaed;
}
.rf-action kbd {
  background: rgba(255, 255, 255, 0.12);
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 0.8rem;
}
.rf-action--remove {
  background: #b3261e;
  border-color: #b3261e;
  color: #fff;
}
.rf-action--remove:hover {
  background: #c5362d;
}
.rf-action--add {
  background: #1e7d44;
  border-color: #1e7d44;
  color: #fff;
}
.rf-action--add:hover {
  background: #258a4d;
}
.rf-action--keep-good {
  background: #1e7d44;
  border-color: #1e7d44;
  color: #fff;
}
.rf-action--keep-good:hover {
  background: #258a4d;
}
.rf-action--keep:hover,
.rf-action--skip:hover,
.rf-action--undo:hover {
  background: #2c2f37;
}
.rf-action--undo:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.rf-action--leftonly {
  background: #2563eb;
  border-color: #2563eb;
  color: #fff;
}
.rf-action--leftonly:hover {
  background: #3b82f6;
}
.rf-action--both {
  background: #b3261e;
  border-color: #b3261e;
  color: #fff;
}
.rf-action--both:hover {
  background: #c5362d;
}
.rf-action--neither {
  background: #1e7d44;
  border-color: #1e7d44;
  color: #fff;
}
.rf-action--neither:hover {
  background: #258a4d;
}
.rf-action--swap {
  background: #9a6b1f;
  border-color: #9a6b1f;
  color: #fff;
}
.rf-action--swap:hover {
  background: #b07d27;
}
.rf-actions-label {
  align-self: center;
  color: #9aa0a6;
  font-size: 0.85rem;
  margin-right: 2px;
}
.rf-pane--flagged .rf-img {
  box-shadow: 0 0 0 2px rgba(179, 38, 30, 0.55);
}
.rf-action--rec {
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.55);
}
.rf-rec-pip {
  font-size: 0.68rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  background: rgba(255, 255, 255, 0.22);
  border-radius: 4px;
  padding: 1px 5px;
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
  gap: 14px;
  flex-wrap: wrap;
  justify-content: center;
  max-width: 760px;
  padding: 8px 14px;
  border: 1px solid #9a6b1f;
  border-radius: 8px;
  background: rgba(154, 107, 31, 0.14);
}
.rf-confirm-msg {
  color: #f1d9ac;
  font-size: 0.9rem;
}
.rf-confirm-actions {
  display: inline-flex;
  gap: 8px;
}
.rf-confirm-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #23252c;
  color: #e8eaed;
  border: 1px solid #34373f;
  border-radius: 6px;
  padding: 5px 12px;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
}
.rf-confirm-btn:hover {
  background: #2c2f37;
}
.rf-confirm-btn--apply {
  background: #9a6b1f;
  border-color: #9a6b1f;
  color: #fff;
}
.rf-confirm-btn--apply:hover {
  background: #b07d27;
}
.rf-confirm-btn kbd {
  background: rgba(255, 255, 255, 0.14);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 0.75rem;
}

/* Passive per-pane consistency chip: subtle, muted, only rendered when there is a prior
   vote for that picture, to nudge consistency before a conflict even happens. */
.rf-vote-hint {
  color: #c9a45c;
  font-size: 0.74rem;
  font-style: italic;
}

.rf-hint {
  color: #80868b;
  font-size: 0.82rem;
}
.rf-hint kbd {
  background: #23252c;
  border: 1px solid #34373f;
  border-radius: 4px;
  padding: 1px 6px;
}

/* Bulk preview modal */
.rf-preview {
  position: fixed;
  /* Below the title bar (0px in a browser) so it never covers the window
     controls; the card centres within the reduced box. */
  inset: var(--titlebar-h) 0 0 0;
  z-index: 4050;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.rf-preview-card {
  display: flex;
  flex-direction: column;
  width: min(1100px, 95vw);
  max-height: 90vh;
  background: #15161a;
  border: 1px solid #34373f;
  border-radius: 12px;
  overflow: hidden;
}
.rf-preview-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid #2a2c33;
  font-weight: 600;
}
.rf-preview-sub {
  display: block;
  font-weight: 400;
  font-size: 0.82rem;
  color: #9aa0a6;
}
.rf-preview-x {
  background: #23252c;
  color: #e8eaed;
  border: 1px solid #34373f;
  border-radius: 6px;
  width: 30px;
  height: 30px;
  cursor: pointer;
  flex: 0 0 auto;
}
.rf-preview-empty {
  padding: 40px;
  text-align: center;
  color: #9aa0a6;
}
.rf-preview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
  padding: 16px 18px;
  overflow: auto;
}
.rf-preview-item {
  margin: 0;
  background: #1b1c21;
  border: 1px solid #2a2c33;
  border-radius: 8px;
  padding: 8px;
}
.rf-preview-imgs {
  display: flex;
  gap: 6px;
}
.rf-preview-img {
  width: 50%;
  height: 150px;
  object-fit: cover;
  border-radius: 6px;
  background: #0c0d10;
  cursor: zoom-in;
}
.rf-preview-img--flagged {
  box-shadow: inset 0 0 0 2px rgba(179, 38, 30, 0.7);
}
.rf-preview-verdict {
  margin-top: 6px;
  text-align: center;
  font-weight: 600;
  font-size: 0.85rem;
  color: #cdd0d6;
}
.rf-preview-foot {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 18px;
  border-top: 1px solid #2a2c33;
}
.rf-preview-note {
  margin-right: auto;
  color: #9aa0a6;
  font-size: 0.85rem;
}
.rf-bulk-preview {
  background: #23252c;
  color: #e8eaed;
  border: 1px solid #34373f;
  border-radius: 6px;
  padding: 5px 12px;
  cursor: pointer;
}
.rf-bulk-preview:hover:not(:disabled) {
  background: #2c2f37;
}
.rf-bulk-preview:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* Full-screen zoom layer */
.rf-zoom {
  position: fixed;
  /* Anchor below the title bar (0px in a browser) so the bar / window controls
     stay usable while zoomed. The reduced-height box is the scroll container, so
     the zoomed image pans/scrolls within the area below the bar. */
  inset: var(--titlebar-h) 0 0 0;
  z-index: 4100;
  background: rgba(0, 0, 0, 0.92);
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
  background: rgba(21, 22, 26, 0.9);
  border: 1px solid #34373f;
  border-radius: 999px;
  padding: 4px 14px;
  color: #cdd0d6;
  font-size: 0.82rem;
  white-space: nowrap;
  transition: opacity 0.3s ease;
}
.rf-zoom-hint--hidden {
  opacity: 0;
}
.rf-zoom-hint kbd {
  background: #23252c;
  border: 1px solid #34373f;
  border-radius: 4px;
  padding: 1px 5px;
}
</style>
