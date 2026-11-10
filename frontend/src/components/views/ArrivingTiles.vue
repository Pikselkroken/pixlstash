<script setup>
// The import happens in the grid (#2512). The moment files are dropped, a
// group of tiles drawn from the files' own bytes appears at the top of the
// current view, under one band that carries the stage and the counts. Each
// tile wears its file's state as a corner mark (never dimming: pending is not
// disabled), a duplicate says so on the tile with a link to the picture it
// matched, and when the import commits the band becomes the receipt until Done,
// the next navigation or import, or its countdown runs out. State lives in
// useImportArrivalsStore, written by ImageImporter; this component draws it and
// owns the receipt's countdown.
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { VIcon } from "vuetify/components";
import { useImportArrivalsStore } from "../../stores/useImportArrivalsStore";
import { useTasksStore } from "../../stores/useTasksStore";
import AppBarButton from "../widgets/AppBarButton.vue";

const props = defineProps({
  // The grid's own column count, so an arriving tile is the size it lands as.
  columns: { type: Number, default: 6 },
});

const emit = defineEmits(["show-picture"]);

// Two rows, then a counter tile: 2,000 tiles at the top of a grid would push
// the library out of sight.
const COLLAPSED_ROWS = 2;

const arrivals = useImportArrivalsStore();
const tasksStore = useTasksStore();

/** runId → true once its counter tile was clicked. */
const expanded = ref({});
/** runId → the receipt chip whose tiles are shown ("duplicate", …). */
const receiptFilter = ref({});

const LEAVES_AT_LANDING = new Set([
  "duplicate",
  "scrapheaped",
  "unsupported",
  "failed",
]);

function count(n) {
  return Number(n || 0).toLocaleString();
}

function plural(n, one, many) {
  return `${count(n)} ${n === 1 ? one : many}`;
}

function bytesPair(sent, total) {
  const units = [
    [1024 ** 3, "GB"],
    [1024 ** 2, "MB"],
    [1024, "KB"],
  ];
  const [size, unit] = units.find(([s]) => total >= s) || [1, "B"];
  const digits = size === 1 ? 0 : 1;
  return `${(sent / size).toFixed(digits)} of ${(total / size).toFixed(digits)} ${unit}`;
}

function stageText(run) {
  if (run.stage === "uploading") return "Uploading";
  if (run.stage === "checking") return "Checking for duplicates";
  if (run.stage === "error") return "Import failed";
  return run.counts.added
    ? plural(run.counts.added, "picture", "pictures")
    : "No new pictures";
}

function sectionLabel(run) {
  return run.stage === "landed" ? "Added just now" : "Arriving";
}

function progressText(run) {
  if (run.stage === "uploading") return bytesPair(run.bytesSent, run.bytesTotal);
  if (run.stage === "checking") {
    return `${count(Math.min(run.checked, run.total))} of ${count(run.total)}`;
  }
  return "";
}

function progressPct(run) {
  if (run.stage === "uploading") {
    return run.bytesTotal ? (run.bytesSent / run.bytesTotal) * 100 : 0;
  }
  return run.total ? (Math.min(run.checked, run.total) / run.total) * 100 : 0;
}

function chips(run) {
  const c = run.counts;
  const landed = run.stage === "landed";
  return [
    !landed && { id: "added", icon: "mdi-check", text: `${count(c.added)} added` },
    {
      id: "duplicate",
      icon: "mdi-content-copy",
      text: `${count(c.duplicate)} already in library`,
      action: "Show",
      n: c.duplicate,
    },
    {
      id: "scrapheaped",
      icon: "mdi-delete-restore",
      text: `${count(c.scrapheaped)} restored from Scrapheap`,
      action: "Show",
      n: c.scrapheaped,
    },
    {
      id: "unsupported",
      icon: "mdi-file-cancel-outline",
      text: `${count(c.unsupported)} not supported`,
      action: "List",
      n: c.unsupported,
    },
    {
      id: "failed",
      icon: "mdi-alert-circle-outline",
      text: `${count(c.failed)} could not be read`,
      action: "List",
      n: c.failed,
    },
  ].filter((chip) => chip && (chip.id === "added" || chip.n > 0));
}

function matchesFilter(tile, filter) {
  return tile.state === filter;
}

/** What the group shows: at landing the ones that did not land leave it. */
function shownTiles(run) {
  if (run.stage !== "landed") return run.tiles;
  const filter = receiptFilter.value[run.id];
  if (filter) return run.tiles.filter((t) => matchesFilter(t, filter));
  return run.tiles.filter((t) => !LEAVES_AT_LANDING.has(t.state));
}

function visibleTiles(run) {
  const tiles = shownTiles(run);
  if (expanded.value[run.id]) return tiles;
  const room = Math.max(1, props.columns * COLLAPSED_ROWS);
  return tiles.length > room ? tiles.slice(0, room - 1) : tiles;
}

function hiddenCount(run) {
  return shownTiles(run).length - visibleTiles(run).length;
}

// ── The receipt's countdown ────────────────────────────────────────────────
// A landed receipt leaves by itself after this long, draining a hairline as
// it goes. A dismissal window, not a motion token (the action receipt's is the
// same kind of value). Hover, focus inside it and a hidden tab pause it
// (WCAG 2.2.1). It never runs on a receipt that reports files the server could
// not read, nor once the receipt was used (a chip filtered, the rest shown):
// those stay until Done.
const RECEIPT_DWELL_MS = 8000;

/** runId → { hover, focus }: whether the receipt is being read. */
const beingRead = new Map();
/** runId → { handle, remaining, startedAt } for each counting receipt. */
const timers = new Map();
/** runId → { key, remaining, running }: what each countdown bar draws. */
const countdowns = ref({});
let countdownKey = 0;
let tabHidden = false;

function countsDown(run) {
  return (
    run.stage === "landed" &&
    !run.counts.failed &&
    !expanded.value[run.id] &&
    // `in`, not truthiness: a filter toggled back off still means it was used.
    !(run.id in receiptFilter.value)
  );
}

/** Start or stop a receipt's timer to match whether anyone is reading it. */
function syncTimer(id) {
  const timer = timers.get(id);
  if (!timer) return;
  const held = beingRead.get(id);
  const run = !tabHidden && !held?.hover && !held?.focus;
  if (run === (timer.handle !== null)) return;
  if (run) {
    timer.startedAt = Date.now();
    timer.handle = setTimeout(() => arrivals.dismiss(id), timer.remaining);
  } else {
    clearTimeout(timer.handle);
    timer.handle = null;
    timer.remaining = Math.max(
      0,
      timer.remaining - (Date.now() - timer.startedAt),
    );
  }
  // A fresh key restarts the bar from the banked time, so it never drifts
  // from the timer (its CSS clock keeps moving in a hidden tab).
  countdowns.value = {
    ...countdowns.value,
    [id]: { key: ++countdownKey, remaining: timer.remaining, running: run },
  };
}

function setReading(run, how, on) {
  beingRead.set(run.id, { ...beingRead.get(run.id), [how]: on });
  syncTimer(run.id);
}

function onFocusOut(run, event) {
  // Focus moving between the receipt's own controls is still reading it.
  if (event.currentTarget?.contains?.(event.relatedTarget)) return;
  setReading(run, "focus", false);
}

function countdownStyle(id) {
  const elapsed = RECEIPT_DWELL_MS - countdowns.value[id].remaining;
  // The duration goes as a property so the reduced-motion rule can restore it.
  return {
    "--arr-dwell": `${RECEIPT_DWELL_MS}ms`,
    animationDelay: `-${elapsed}ms`,
  };
}

watch(
  () => arrivals.runs.filter(countsDown).map((run) => run.id),
  (ids) => {
    for (const [id, timer] of timers) {
      if (ids.includes(id)) continue;
      clearTimeout(timer.handle);
      timers.delete(id);
    }
    for (const id of ids) {
      if (timers.has(id)) continue;
      timers.set(id, {
        handle: null,
        remaining: RECEIPT_DWELL_MS,
        startedAt: 0,
      });
      syncTimer(id);
    }
    countdowns.value = Object.fromEntries(
      Object.entries(countdowns.value).filter(([id]) => timers.has(id)),
    );
  },
  { immediate: true },
);

function onVisibilityChange() {
  tabHidden = document.hidden;
  for (const id of timers.keys()) syncTimer(id);
}

onMounted(() => {
  document.addEventListener("visibilitychange", onVisibilityChange);
  onVisibilityChange();
});

function toggleFilter(run, chip) {
  if (run.stage !== "landed" || !chip.action) return;
  const current = receiptFilter.value[run.id];
  receiptFilter.value = {
    ...receiptFilter.value,
    [run.id]: current === chip.id ? null : chip.id,
  };
}

function expand(run) {
  expanded.value = { ...expanded.value, [run.id]: true };
}

function done(run) {
  arrivals.dismiss(run.id);
}

function cancel(run) {
  tasksStore.abortImportRun(run.id);
}

/** A preview the browser cannot decode falls back to the named tile. */
const broken = ref(new Set());
function markBroken(tile) {
  broken.value = new Set(broken.value).add(tile.key);
}

function markIcon(tile) {
  if (tile.state === "uploading") return "mdi-tray-arrow-up";
  if (tile.state === "landed") return "mdi-check";
  return "";
}

function tileLabel(tile) {
  const what = {
    uploading: "uploading",
    checking: "checking for duplicates",
    new: "new, importing",
    landed: "added",
    duplicate: "already in library",
    scrapheaped: "restored from the Scrapheap",
    unsupported: "not a supported format",
    failed: "could not be read",
  }[tile.state];
  return `${tile.name}: ${what}`;
}

const showReading = computed(
  () => arrivals.reading.active && arrivals.runs.length === 0,
);

// A run that leaves forgets its view state.
watch(
  () => arrivals.runs.map((run) => run.id),
  (ids) => {
    const keep = (map) =>
      Object.fromEntries(Object.entries(map).filter(([id]) => ids.includes(id)));
    expanded.value = keep(expanded.value);
    receiptFilter.value = keep(receiptFilter.value);
    for (const id of beingRead.keys()) {
      if (!ids.includes(id)) beingRead.delete(id);
    }
  },
);

onBeforeUnmount(() => {
  broken.value = new Set();
  document.removeEventListener("visibilitychange", onVisibilityChange);
  for (const timer of timers.values()) clearTimeout(timer.handle);
  timers.clear();
});
</script>

<template>
  <div
    v-if="showReading || arrivals.runs.length"
    class="arr"
    data-testid="arriving-tiles"
  >
    <div v-if="showReading" class="arr-band" role="status">
      <span class="arr-label">Arriving</span>
      <span class="arr-what">Reading files…</span>
      <div class="arr-track"><i style="width: 0%"></i></div>
    </div>
    <section
      v-for="run in arrivals.runs"
      :key="run.id"
      class="arr-run"
      :class="`arr-run--${run.stage}`"
      :data-arriving-run="run.id"
      :aria-label="`${sectionLabel(run)}: ${stageText(run)}`"
      @mouseenter="setReading(run, 'hover', true)"
      @mouseleave="setReading(run, 'hover', false)"
      @focusin="setReading(run, 'focus', true)"
      @focusout="onFocusOut(run, $event)"
    >
      <div class="arr-band">
        <span class="arr-label">{{
          run.stage === "error" ? "Arriving" : sectionLabel(run)
        }}</span>
        <!-- Only the stage words are live: the counts change every poll, and
             a 2,000-file import would never stop talking. -->
        <span class="arr-what" role="status" aria-live="polite">{{
          stageText(run)
        }}</span>
        <span v-if="progressText(run)" class="arr-of">{{
          progressText(run)
        }}</span>
        <span v-if="run.stage === 'error'" class="arr-error">{{
          run.error
        }}</span>
        <div
          v-if="run.stage === 'uploading' || run.stage === 'checking'"
          class="arr-track"
        >
          <i :style="{ width: `${progressPct(run)}%` }"></i>
        </div>
        <span v-else class="arr-grow"></span>
        <div
          v-if="run.stage !== 'uploading' && run.stage !== 'error'"
          class="arr-chips"
        >
          <component
            :is="run.stage === 'landed' && chip.action ? 'button' : 'span'"
            v-for="chip in chips(run)"
            :key="chip.id"
            class="arr-chip"
            :class="{ 'arr-chip--on': receiptFilter[run.id] === chip.id }"
            :type="run.stage === 'landed' && chip.action ? 'button' : undefined"
            :aria-pressed="
              run.stage === 'landed' && chip.action
                ? String(receiptFilter[run.id] === chip.id)
                : undefined
            "
            @click="toggleFilter(run, chip)"
          >
            <VIcon size="14">{{ chip.icon }}</VIcon>
            {{ chip.text
            }}<template v-if="run.stage === 'landed' && chip.action">
              · {{ chip.action }}</template
            >
          </component>
        </div>
        <AppBarButton v-if="run.stage === 'uploading'" @click="cancel(run)">
          Cancel
        </AppBarButton>
        <AppBarButton
          v-if="run.stage === 'landed' || run.stage === 'error'"
          @click="done(run)"
        >
          {{ run.stage === "error" ? "Dismiss" : "Done" }}
        </AppBarButton>
        <span
          v-if="countdowns[run.id]"
          :key="countdowns[run.id].key"
          class="arr-countdown"
          :class="{ 'arr-countdown--paused': !countdowns[run.id].running }"
          data-testid="arriving-countdown"
          aria-hidden="true"
          ><i :style="countdownStyle(run.id)"></i
        ></span>
      </div>
      <div v-if="run.stage === 'uploading'" class="arr-note">
        <VIcon size="14">mdi-tray-arrow-up</VIcon>
        Keep this tab open until the upload finishes
      </div>
      <div
        v-if="run.stage !== 'error' && shownTiles(run).length"
        class="arr-tiles"
        :style="{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }"
      >
        <div
          v-for="tile in visibleTiles(run)"
          :key="tile.key"
          class="arr-tile"
          :class="`arr-tile--${tile.state}`"
          :data-arriving-state="tile.state"
        >
          <img
            v-if="tile.previewUrl && !broken.has(tile.key)"
            :src="tile.previewUrl"
            :alt="tileLabel(tile)"
            loading="lazy"
            decoding="async"
            draggable="false"
            @error="markBroken(tile)"
          />
          <div
            v-else
            class="arr-named"
            role="img"
            :aria-label="tileLabel(tile)"
          >
            <VIcon size="28">{{
              tile.state === "unsupported"
                ? "mdi-file-cancel-outline"
                : tile.kind === "video"
                  ? "mdi-filmstrip"
                  : tile.kind === "archive"
                    ? "mdi-folder-zip-outline"
                    : "mdi-image-outline"
            }}</VIcon>
            <span class="arr-fn">{{ tile.name }}</span>
            <span v-if="tile.state === 'unsupported'">Not a supported format</span>
            <span v-else-if="tile.state === 'failed'">Could not be read</span>
          </div>
          <span
            v-if="tile.state === 'checking' || tile.state === 'new'"
            class="arr-mark"
            aria-hidden="true"
          >
            <VIcon size="14" class="mdi-spin">mdi-loading</VIcon>
          </span>
          <span
            v-else-if="markIcon(tile)"
            class="arr-mark"
            :class="{ 'arr-mark--beat': tile.state === 'landed' }"
            aria-hidden="true"
          >
            <VIcon size="14">{{ markIcon(tile) }}</VIcon>
          </span>
          <div
            v-if="
              tile.state === 'duplicate' ||
              tile.state === 'scrapheaped' ||
              (tile.state === 'failed' && tile.previewUrl)
            "
            class="arr-strip"
          >
            <VIcon size="14">{{
              tile.state === "duplicate"
                ? "mdi-content-copy"
                : tile.state === "failed"
                  ? "mdi-alert-circle-outline"
                  : "mdi-delete-restore"
            }}</VIcon>
            {{
              tile.state === "duplicate"
                ? "In library"
                : tile.state === "failed"
                  ? "Could not be read"
                  : "Restored"
            }}
            <button
              v-if="tile.state !== 'failed' && tile.pictureId"
              type="button"
              class="arr-strip-link"
              @click="emit('show-picture', tile.pictureId)"
            >
              Show
            </button>
          </div>
        </div>
        <button
          v-if="hiddenCount(run) > 0"
          type="button"
          class="arr-tile arr-more"
          :aria-label="`Show ${count(hiddenCount(run))} more`"
          @click="expand(run)"
        >
          <span class="arr-more-n">+{{ count(hiddenCount(run)) }}</span>
          <span class="arr-more-sub">{{
            run.stage === "landed" ? "more" : "still arriving"
          }}</span>
        </button>
      </div>
    </section>
    <div class="arr-sep"><span class="arr-label">Earlier</span></div>
  </div>
</template>

<style scoped>
.arr {
  padding: var(--space-4) var(--space-4) 0;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  max-height: 60vh;
  overflow-y: auto;
}

.arr-run + .arr-run {
  margin-top: var(--space-5);
}

.arr-band {
  position: relative;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  min-height: var(--control-h-bar);
  margin-bottom: var(--space-3);
}

.arr-label {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  opacity: var(--opacity-text-secondary);
  white-space: nowrap;
}

.arr-what {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.arr-of {
  font-size: var(--text-sm);
  opacity: var(--opacity-text-secondary);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.arr-error {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-error));
}

.arr-track {
  flex: 1;
  min-width: calc(2 * var(--space-9));
  height: var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--track-trough);
  overflow: hidden;
  position: relative;
}

.arr-track i {
  position: absolute;
  inset: 0 auto 0 0;
  background: rgb(var(--v-theme-tertiary));
  border-radius: var(--radius-pill);
  transition: width var(--dur-2) var(--ease-standard);
}

/* The receipt's countdown: the drain hairline along the band's foot. scaleX,
   never an animated width, so the band does not relayout every frame. The
   timer in the script is the truth; the delay starts the drain where it is. */
.arr-countdown {
  position: absolute;
  inset: auto 0 0 0;
  height: var(--countdown-h);
  border-radius: var(--radius-pill);
  background: var(--track-trough);
  overflow: hidden;
  pointer-events: none;
}

.arr-countdown i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: rgb(var(--v-theme-tertiary));
  transform-origin: left center;
  animation-name: arr-drain;
  animation-duration: var(--arr-dwell);
  animation-timing-function: linear;
  animation-fill-mode: forwards;
}

.arr-countdown--paused i {
  animation-play-state: paused;
}

@keyframes arr-drain {
  from {
    transform: scaleX(1);
  }
  to {
    transform: scaleX(0);
  }
}

.arr-grow {
  flex: 1;
}

.arr-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.arr-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--control-h-sm);
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  border: 1px solid transparent;
  background: rgb(var(--v-theme-cancel-button));
  color: rgb(var(--v-theme-cancel-button-text));
  font: inherit;
  font-size: var(--text-xs);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.arr-chip .v-icon {
  opacity: var(--opacity-text-secondary);
}

button.arr-chip {
  cursor: pointer;
}

button.arr-chip:hover {
  background-image: linear-gradient(var(--hover-wash), var(--hover-wash));
}

.arr-chip--on {
  background-image: linear-gradient(var(--active-wash), var(--active-wash));
  box-shadow: inset 0 0 0 1px var(--active-bar);
}

.arr-note {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: calc(-1 * var(--space-2)) 0 var(--space-3);
  font-size: var(--text-xs);
  opacity: var(--opacity-text-secondary);
}

.arr-tiles {
  display: grid;
  gap: var(--space-3);
}

.arr-tile {
  aspect-ratio: 1 / 1;
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-surface));
}

.arr-tile img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.arr-named {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-3);
  text-align: center;
  font-size: var(--text-xs);
}

.arr-named .v-icon,
.arr-named span:not(.arr-fn) {
  opacity: var(--opacity-text-secondary);
}

.arr-fn {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The tile state family: a corner mark the size of the grid's count badges,
   and a panel strip for a duplicate. */
.arr-mark {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  border-radius: 50%;
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-surface));
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--elevation-2);
}

/* Landed: a check for one beat, then nothing - the picture is just a picture. */
.arr-mark--beat {
  animation: arr-beat var(--dur-3) var(--ease-standard) 1.2s forwards;
}

@keyframes arr-beat {
  to {
    opacity: 0;
  }
}

.arr-strip {
  position: absolute;
  inset: auto 0 0 0;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-xs);
  white-space: nowrap;
}

.arr-strip .v-icon {
  opacity: var(--opacity-text-secondary);
}

.arr-strip-link {
  margin-left: auto;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: none;
  color: inherit;
  font: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
  padding: 0 var(--space-1);
}

.arr-more {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.arr-more:hover {
  background-image: linear-gradient(var(--hover-wash), var(--hover-wash));
}

.arr-more-n {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.arr-more-sub {
  font-size: var(--text-xs);
  opacity: var(--opacity-text-secondary);
}

.arr-sep {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-top: var(--space-5);
  padding-bottom: var(--space-2);
}

.arr-sep::after {
  content: "";
  flex: 1;
  height: 1px;
  background: rgb(var(--v-theme-divider));
}

@media (prefers-reduced-motion: reduce) {
  /* The drain stays, as the action receipt's does (WCAG 2.3.3): it is the
     time-left readout, and the global collapse would leave it at empty over a
     receipt that is still up. */
  .arr-countdown i {
    animation-duration: var(--arr-dwell) !important;
  }

  .arr-mark--beat {
    animation-delay: 1.2s;
    animation-duration: 0s;
  }
}
</style>
