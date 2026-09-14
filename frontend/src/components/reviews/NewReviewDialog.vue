<template>
  <div class="rs-dialog-backdrop" @click.self="emit('close')">
    <div
      class="rs-dialog"
      role="dialog"
      aria-modal="true"
      aria-label="New review"
    >
      <h3 class="rs-dialog-title">New review</h3>

      <label class="rs-dialog-field">
        <span class="rs-dialog-label">
          Tag
          <span class="rs-dialog-label-note"
            >({{ visible.length }} of {{ allRows.length }} tags · one open
            review per tag)</span
          >
        </span>
        <div class="rs-dialog-tagbar">
          <div class="rs-dialog-search">
            <v-icon size="16" class="rs-dialog-search-icon">mdi-magnify</v-icon>
            <input
              ref="searchRef"
              v-model="q"
              type="text"
              placeholder="Filter tags…"
              @keydown.escape.stop.prevent="emit('close')"
            />
          </div>
          <Segmented
            v-model="order"
            class="rs-dialog-order"
            :options="ORDER_OPTIONS"
            aria-label="Tag order"
          />
        </div>
        <div class="rs-dialog-chips">
          <button
            v-for="h in visible"
            :key="h.tag"
            class="rs-dialog-chip"
            :class="{
              'rs-dialog-chip--active': tag === h.tag,
              'rs-dialog-chip--open': openTags.has(h.tag),
              'rs-dialog-chip--anomaly': store.isAnomalyTag(h.tag),
            }"
            type="button"
            @click="pickTag(h.tag)"
          >
            <Tooltip
              :text="
                openTags.has(h.tag) ? 'Already open - jump to the session' : ''
              "
              activator="parent"
            />
            <v-icon
              v-if="store.isAnomalyTag(h.tag)"
              size="12"
              class="rs-dialog-chip-flag"
              >mdi-alert-octagon-outline</v-icon
            >
            {{ h.tag }}{{ openTags.has(h.tag) ? " · open" : "" }}
          </button>
          <span v-if="!visible.length" class="rs-dialog-nomatch"
            >No tags match “{{ q }}”.</span
          >
        </div>
      </label>

      <div class="rs-dialog-scopes">
        <label class="rs-dialog-scope">
          <span class="rs-dialog-label">Project</span>
          <select v-model="projectId" @change="$event.target.blur()">
            <option :value="null">Any</option>
            <option v-for="p in store.projects" :key="p.id" :value="p.id">
              {{ p.name || `Project ${p.id}` }}
            </option>
          </select>
        </label>
        <!-- Set scope is a custom listbox (not a native <select>) because a
             <select>'s <option>s can't render the lock icon a locked set needs.
             Locked sets render greyed, with mdi-lock-outline, and are not
             selectable (their pictures are read-only, so they can't be reviewed
             - the backend also 423s a locked set_id as a backstop). -->
        <div class="rs-dialog-scope">
          <span id="rs-set-label" class="rs-dialog-label">Set</span>
          <div
            ref="setBoxRef"
            class="rs-listbox"
            @focusout="onSetFocusOut"
            @keydown.escape.stop.prevent="closeSetMenu(true)"
          >
            <button
              ref="setTriggerRef"
              type="button"
              class="rs-listbox-trigger"
              :class="{ 'rs-listbox-trigger--locked': selectedSetLocked }"
              role="combobox"
              aria-haspopup="listbox"
              aria-labelledby="rs-set-label"
              :aria-expanded="setMenuOpen"
              :title="selectedSetLocked ? undefined : selectedSetLabel"
              @click="toggleSetMenu"
              @keydown="onTriggerKeydown"
            >
              <Tooltip
                :text="
                  selectedSetLocked ? lockedSetTitle(selectedSetLabel) : ''
                "
                activator="parent"
              />
              <v-icon
                v-if="selectedSetLocked"
                size="14"
                class="rs-listbox-trigger-lock"
                >mdi-lock-outline</v-icon
              >
              <span class="rs-listbox-value">{{ selectedSetLabel }}</span>
              <v-icon size="16" class="rs-listbox-caret"
                >mdi-chevron-down</v-icon
              >
            </button>
            <ul
              v-if="setMenuOpen"
              ref="setListRef"
              class="rs-listbox-menu"
              role="listbox"
              tabindex="-1"
              aria-labelledby="rs-set-label"
              :aria-activedescendant="`rs-set-opt-${activeSetIndex}`"
              @keydown="onListKeydown"
            >
              <li
                v-for="(opt, i) in setOptions"
                :id="`rs-set-opt-${i}`"
                :key="opt.id ?? 'any'"
                class="rs-listbox-option"
                :class="{
                  'rs-listbox-option--active': i === activeSetIndex,
                  'rs-listbox-option--selected': opt.id === setId,
                  'rs-listbox-option--locked': opt.locked,
                }"
                role="option"
                :aria-selected="opt.id === setId"
                :aria-disabled="opt.locked || undefined"
                @click="selectSet(opt)"
                @mousemove="activeSetIndex = i"
              >
                <Tooltip
                  :text="opt.locked ? lockedSetTitle(opt.name) : ''"
                  activator="parent"
                />
                <v-icon v-if="opt.locked" size="16" class="rs-listbox-lock"
                  >mdi-lock-outline</v-icon
                >
                <!-- Locked rows carry the fuller lock explanation as the row's
                     tip; a native title here would open beside it. -->
                <span
                  class="rs-listbox-option-label"
                  :title="opt.locked ? undefined : opt.name"
                  >{{ opt.name }}</span
                >
                <v-icon
                  v-if="opt.id === setId"
                  size="16"
                  class="rs-listbox-check"
                  >mdi-check</v-icon
                >
              </li>
            </ul>
          </div>
        </div>
        <label class="rs-dialog-scope">
          <span class="rs-dialog-label">Character</span>
          <select v-model="characterId" @change="$event.target.blur()">
            <option :value="null">Any</option>
            <option value="UNASSIGNED">Unassigned</option>
            <option v-for="c in store.characters" :key="c.id" :value="c.id">
              {{ c.name || `Character ${c.id}` }}
            </option>
          </select>
        </label>
      </div>
      <p class="rs-dialog-frozen">
        Scope is frozen when the review is created - a different scope is a
        different review.
      </p>

      <div v-if="tag" class="rs-dialog-preview">
        <div class="rs-dialog-preview-title">
          <v-icon size="16" class="rs-dialog-preview-icon">mdi-radar</v-icon>
          Scan preview
        </div>
        <div class="rs-dialog-preview-body">
          The near-neighbour scan runs once, on create; its report becomes this
          review’s cover sheet.
        </div>
        <label class="rs-dialog-include">
          <input v-model="includeReviewed" type="checkbox" />
          <span
            >Include suspects handled in earlier reviews
            <span class="rs-dialog-include-note"
              >(normally left out - this re-surfaces them)</span
            ></span
          >
        </label>
      </div>

      <p v-if="store.createError" class="rs-dialog-error">
        {{ store.createError }}
      </p>

      <div class="rs-dialog-actions">
        <button class="rs-dialog-btn" type="button" @click="emit('close')">
          Cancel
        </button>
        <button
          class="rs-dialog-btn rs-dialog-btn--go"
          type="button"
          :disabled="!tag || store.creating || selectedSetLocked"
          @click="create"
        >
          <Tooltip
            :text="selectedSetLocked ? lockedSetTitle(selectedSetLabel) : ''"
            activator="parent"
          />
          <v-icon size="16">{{
            store.creating ? "mdi-loading mdi-spin" : "mdi-radar"
          }}</v-icon>
          Scan &amp; create
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
// New-review dialog: explicit creation. Open tags stay ENABLED - clicking one
// jumps to the open session instead of dead-ending on a disabled chip.
import { computed, nextTick, onMounted, ref } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import Segmented from "../widgets/Segmented.vue";
// Shared with TagHealthBoard's locked-scope state so both surfaces explain a
// locked set with the same sentence.
import { lockedSetTitle } from "./lockedSetCopy";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  preset: { type: String, default: "" },
  // Prefilled from the app's launch context (project/set/character selection).
  initialScope: {
    type: Object,
    default: () => ({ projectId: null, setId: null, characterId: null }),
  },
});
const emit = defineEmits(["close"]);

const store = useReviewSessionsStore();

const tag = ref(props.preset || "");
const q = ref("");
const order = ref("suggested");
const ORDER_OPTIONS = [
  { id: "suggested", label: "Suggested" },
  { id: "alpha", label: "Alphabetical" },
];
const includeReviewed = ref(false);
const projectId = ref(props.initialScope.projectId ?? null);
const setId = ref(props.initialScope.setId ?? null);
const characterId = ref(props.initialScope.characterId ?? null);
const searchRef = ref(null);

onMounted(() => searchRef.value?.focus());

const openTags = computed(() => new Set(store.sessions.map((s) => s.tag)));

const allRows = computed(() => store.healthRows);

function corrections(r) {
  return (r.est_wrong ?? 0) + (r.est_missing ?? 0) + (r.mismatch ?? 0);
}

const visible = computed(() => {
  const needle = q.value.trim().toLowerCase();
  return allRows.value
    .filter((h) => !needle || h.tag.toLowerCase().includes(needle))
    .slice()
    .sort((a, b) =>
      order.value === "alpha"
        ? a.tag.localeCompare(b.tag)
        : corrections(b) - corrections(a) || a.tag.localeCompare(b.tag),
    );
});

// --- Set-scope listbox --------------------------------------------------------
//
// A custom listbox (rather than a native <select>) so a locked set can render a
// lock icon and a greyed, non-selectable row. `store.sets` carries `locked`
// straight from the API (PictureSetResponse.locked via safe_model_dict), so the
// dialog reads set-level lock state directly - no extra lookup store needed.
const setMenuOpen = ref(false);
const activeSetIndex = ref(0);
const setBoxRef = ref(null);
const setTriggerRef = ref(null);
const setListRef = ref(null);

// "Any" + every set, each tagged with its lock state.
const setOptions = computed(() => [
  { id: null, name: "Any", locked: false },
  ...store.sets.map((s) => ({
    id: s.id,
    name: s.name || `Set ${s.id}`,
    locked: !!s.locked,
  })),
]);

const selectedSetLabel = computed(
  () => setOptions.value.find((o) => o.id === setId.value)?.name ?? "Any",
);

// `initialScope` can prefill a locked setId straight into `setId`, bypassing the
// click-time guard in `selectSet()`. Without this the trigger would show the
// locked set as an ordinary selection and the user would only discover the block
// on submit - so the trigger mirrors the locked row's own treatment.
const selectedSetLocked = computed(
  () => setOptions.value.find((o) => o.id === setId.value)?.locked ?? false,
);

// Arrow-key traversal visits EVERY option, locked ones included. That is the
// standard listbox contract: traversal reaches a disabled row so its
// `aria-disabled` state and lock explanation are discoverable by a keyboard-only
// user; only *activation* is blocked (see `selectSet`). Wraps at both ends.
function wrapIndex(i) {
  const n = setOptions.value.length;
  if (!n) return -1;
  return ((i % n) + n) % n;
}

function openSetMenu() {
  if (setMenuOpen.value) return;
  // Land on the current selection (even when locked - it can be prefilled from
  // the launch context), else the first row.
  const sel = setOptions.value.findIndex((o) => o.id === setId.value);
  activeSetIndex.value = sel >= 0 ? sel : 0;
  setMenuOpen.value = true;
  nextTick(() => setListRef.value?.focus());
}

function closeSetMenu(returnFocus = false) {
  if (!setMenuOpen.value) return;
  setMenuOpen.value = false;
  if (returnFocus) nextTick(() => setTriggerRef.value?.focus());
}

function toggleSetMenu() {
  if (setMenuOpen.value) closeSetMenu();
  else openSetMenu();
}

function selectSet(opt) {
  if (opt.locked) return; // Locked sets are not selectable.
  setId.value = opt.id;
  closeSetMenu(true);
}

function onTriggerKeydown(event) {
  if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
    event.preventDefault();
    openSetMenu();
  }
}

function onListKeydown(event) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    const next = wrapIndex(activeSetIndex.value + 1);
    if (next >= 0) activeSetIndex.value = next;
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    const prev = wrapIndex(activeSetIndex.value - 1);
    if (prev >= 0) activeSetIndex.value = prev;
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    selectSet(setOptions.value[activeSetIndex.value]);
  } else if (event.key === "Home") {
    event.preventDefault();
    activeSetIndex.value = 0;
  } else if (event.key === "End") {
    event.preventDefault();
    activeSetIndex.value = Math.max(setOptions.value.length - 1, 0);
  }
}

// Close on focus leaving the whole control (click-outside or tab-away). Escape
// is handled separately so it can also return focus to the trigger.
function onSetFocusOut(event) {
  const next = event.relatedTarget;
  if (next && setBoxRef.value?.contains(next)) return;
  closeSetMenu();
}

function pickTag(t) {
  const open = store.sessions.find((s) => s.tag === t);
  if (open) {
    // Already open: jump to it instead of dead-ending.
    store.openSession(open.id);
    emit("close");
    return;
  }
  tag.value = t;
}

async function create() {
  const review = await store.createReview({
    tag: tag.value,
    projectId: projectId.value,
    setId: setId.value,
    characterId: characterId.value,
    includeReviewed: includeReviewed.value,
  });
  if (review) emit("close");
}
</script>

<style scoped>
.rs-dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
}
.rs-dialog {
  width: 480px;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 64px);
  overflow-y: auto;
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: var(--radius-md);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
/* Dark in both themes: the global ring's width and gap, in the on-dark ink. */
.rs-dialog :focus-visible {
  outline-color: rgb(var(--v-theme-on-dark-surface));
}
.rs-dialog-title {
  font-size: var(--text-md);
  font-weight: var(--weight-bold);
}
.rs-dialog-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rs-dialog-label {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-dialog-label-note {
  text-transform: none;
  letter-spacing: 0;
}
.rs-dialog-tagbar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rs-dialog-search {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
}
.rs-dialog-search-icon {
  position: absolute;
  left: 9px;
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  pointer-events: none;
}
.rs-dialog-search input {
  width: 100%;
  height: 30px;
  padding: 0 10px 0 30px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-sm);
}
/* Dark in both themes, so Segmented's on-surface ink and track take the
   dark-surface values (the dark theme's own) instead of the light theme's. */
.rs-dialog-order {
  flex-shrink: 0;
  --v-theme-on-surface: var(--v-theme-on-dark-surface);
  --hover-wash: rgba(var(--v-theme-on-dark-surface), 0.16);
  --track-trough: rgba(var(--v-theme-scrim), 0.34);
  --track-ring: rgba(var(--v-theme-on-dark-surface), 0.4);
  --focus-stroke: rgb(var(--v-theme-on-dark-surface));
  /* The selected fill too. The deep olive is a dark fill on a dark trough and
     the elevation that carries the second cue is invisible on near-black; the
     bright olive is the dark-theme value for exactly this, under the surface's
     own near-black ink (5.50:1 on #242628, 6.25:1 on #181b20 - main.js). */
  --v-theme-primary: var(--v-theme-dark-surface-primary);
  --v-theme-on-primary: var(--v-theme-dark-surface);
}
.rs-dialog-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-height: 190px;
  overflow-y: auto;
  padding: 2px;
}
.rs-dialog-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 11px;
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-dialog-chip--active {
  border-color: rgb(var(--v-theme-dark-surface-primary));
  background: rgba(var(--v-theme-dark-surface-primary), 0.2);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-dialog-chip--open {
  opacity: 0.55;
}
.rs-dialog-chip--anomaly {
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-dialog-chip-flag {
  flex-shrink: 0;
}
.rs-dialog-nomatch {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  padding: 4px 2px;
}

.rs-dialog-scopes {
  display: flex;
  gap: 8px;
}
.rs-dialog-scope {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.rs-dialog-scope select {
  height: 30px;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-xs);
  cursor: pointer;
  color-scheme: dark;
}
.rs-dialog-scope option {
  background-color: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
}

/* Set-scope listbox - trigger mirrors the sibling native <select>s so the three
   scope controls line up; the menu adds the lock affordance a <select> can't. */
.rs-listbox {
  position: relative;
}
.rs-listbox-trigger {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  height: 30px;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-xs);
  text-align: left;
}
/* A locked set can be prefilled from the launch context, so the trigger takes
   the SAME greyed treatment as `.rs-listbox-option--locked` (not the lighter
   informational `.rs-rail-scope-lock` badge) - it is a blocked selection, not a
   note. */
.rs-listbox-trigger--locked {
  color: rgba(var(--v-theme-on-dark-surface), 0.38);
}
.rs-listbox-trigger-lock {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-dark-surface), 0.38);
}
.rs-listbox-value {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rs-listbox-caret {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
}
.rs-listbox-menu {
  position: absolute;
  z-index: var(--z-raised);
  top: calc(100% + var(--space-1));
  left: 0;
  right: 0;
  margin: 0;
  padding: var(--space-1);
  list-style: none;
  max-height: 220px;
  overflow-y: auto;
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgb(var(--v-theme-dark-surface));
  box-shadow: var(--elevation-2);
}
.rs-listbox-option {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-dark-surface));
  cursor: pointer;
}
.rs-listbox-option-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* The keyboard's current option reads as hover; the chosen value is marked
   by its olive check, and its words stay ink. */
.rs-listbox-option--active {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}
.rs-listbox-check {
  flex-shrink: 0;
  color: rgb(var(--v-theme-dark-surface-primary));
}
/* Locked sets: greyed, non-interactive cursor, lock glyph. Kept legible enough
   to read the name (why it's disabled), per the disabled-state token guidance. */
.rs-listbox-option--locked {
  color: rgba(var(--v-theme-on-dark-surface), 0.38);
  cursor: not-allowed;
}
.rs-listbox-option--locked.rs-listbox-option--active {
  background: transparent;
}
.rs-listbox-lock {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-dark-surface), 0.38);
}
.rs-dialog-frozen {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  margin-top: -6px;
}

.rs-dialog-preview {
  padding: 10px 13px;
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-dark-surface), 0.05);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  font-size: var(--text-sm);
}
.rs-dialog-preview-title {
  font-weight: var(--weight-semibold);
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 5px;
}
.rs-dialog-preview-icon {
  color: rgb(var(--v-theme-accent));
}
.rs-dialog-preview-body {
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
}
.rs-dialog-include {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  cursor: pointer;
}
.rs-dialog-include input {
  accent-color: rgb(var(--v-theme-primary));
}
.rs-dialog-include-note {
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  font-size: var(--text-xs);
}

.rs-dialog-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-dark-surface-error));
}

.rs-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.rs-dialog-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 34px;
  padding: 0 14px;
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-dialog-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.rs-dialog-btn--go {
  border-color: rgb(var(--v-theme-accent));
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 16%, transparent);
  color: rgb(var(--v-theme-accent));
}
</style>
