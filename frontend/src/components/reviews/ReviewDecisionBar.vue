<template>
  <!-- One shared, right-anchored decision bar for BOTH card types. Constant
       button slots per kind, Undo ALWAYS rendered (disabled when the history
       is empty), no wrapping - the buttons never move mid-loop. -->
  <div class="rs-decide" role="toolbar" aria-label="Decision">
    <!-- Lock note. A card can be frozen on EITHER side (the suspect's set, or
         the twin's), and the two block different corners - so the note names the
         set and the buttons that cannot succeed point at it via
         aria-describedby. It is marked `aria-disabled`, never `disabled`: a
         `disabled` button leaves the tab order, so a keyboard user could never
         reach the control to discover why it does nothing. Skip stays fully
         live - it is the way past a locked card. -->
    <span
      v-if="lockNote"
      :id="chipId"
      class="rs-decide-lock"
      :class="{ 'rs-decide-lock--flash': flashing }"
      :title="lockDetail || lockNote"
    >
      <v-icon size="15">mdi-lock-outline</v-icon>
      <span>{{ lockNote }}</span>
    </span>

    <template v-if="kind === 'binary'">
      <button
        class="rs-decide-btn rs-decide-btn--yes"
        type="button"
        :disabled="hold"
        v-bind="lockAttrs('yes')"
        @click="emit('answer', 'yes')"
      >
        <kbd>Y</kbd>
        <span class="rs-decide-verb">Yes</span>
        <span class="rs-decide-sub">{{
          direction === "remove" ? "keep the tag" : "add the tag"
        }}</span>
      </button>
      <button
        class="rs-decide-btn rs-decide-btn--no"
        type="button"
        :disabled="hold"
        v-bind="lockAttrs('no')"
        @click="emit('answer', 'no')"
      >
        <kbd>N</kbd>
        <span class="rs-decide-verb">No</span>
        <span class="rs-decide-sub">{{
          direction === "remove" ? "remove the tag" : "leave untagged"
        }}</span>
      </button>
    </template>

    <template v-else>
      <button
        class="rs-decide-btn rs-decide-btn--yes"
        type="button"
        :disabled="hold"
        v-bind="lockAttrs('both')"
        @click="emit('corner', 'both')"
      >
        <kbd>B</kbd>
        <span class="rs-decide-verb">Both</span>
        <span class="rs-decide-sub">tag both versions</span>
      </button>
      <button
        class="rs-decide-btn rs-decide-btn--no"
        type="button"
        :disabled="hold"
        v-bind="lockAttrs('neither')"
        @click="emit('corner', 'neither')"
      >
        <kbd>N</kbd>
        <span class="rs-decide-verb">Neither</span>
        <span class="rs-decide-sub">clear the tag</span>
      </button>
      <button
        class="rs-decide-btn"
        type="button"
        :disabled="hold"
        v-bind="lockAttrs('left')"
        @click="emit('corner', 'left')"
      >
        <kbd>L</kbd>
        <span class="rs-decide-verb">Left only</span>
        <span class="rs-decide-sub">keep as is</span>
      </button>
      <button
        class="rs-decide-btn"
        type="button"
        :disabled="hold"
        v-bind="lockAttrs('right')"
        @click="emit('corner', 'right')"
      >
        <kbd>R</kbd>
        <span class="rs-decide-verb">Right only</span>
        <span class="rs-decide-sub">move the tag</span>
      </button>
    </template>

    <span class="rs-decide-sep" aria-hidden="true"></span>

    <button
      class="rs-decide-btn"
      type="button"
      title="Can't decide - the card leaves the queue with no change made. Undo brings it back."
      @click="emit('skip')"
    >
      <kbd>S</kbd>
      <span class="rs-decide-verb">Skip</span>
    </button>
    <!-- Undo. `disabled` when there is simply nothing to undo (no reason to
         explain), but `aria-disabled` + the lock reason when a decision EXISTS
         and cannot be reopened - reopen guards both sides of the card, so a
         decision on a locked-twin card is final until the set is unlocked. -->
    <button
      class="rs-decide-btn"
      type="button"
      :disabled="!canUndo"
      :title="undoBlocked ? undefined : undoTitle"
      :aria-keyshortcuts="undoKeyShortcuts"
      v-bind="lockAttrs('undo')"
      @click="emit('undo')"
    >
      <kbd>U</kbd>
      <span class="rs-decide-verb">Undo</span>
    </button>

    <span class="rs-decide-gap" aria-hidden="true"></span>

    <label
      class="rs-gamify"
      :class="{ 'rs-gamify--on': gamify }"
      title="Fireworks, stars, XP, sticker rewards, and relentless praise for doing data cleanup"
    >
      <input
        type="checkbox"
        :checked="gamify"
        @change="emit('gamify-toggle', $event.target.checked)"
      />
      <span class="rs-gamify-label">Pretend this is fun</span>
      <span class="rs-gamify-emoji">{{ gamify ? "🎉" : "" }}</span>
    </label>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, useId, watch } from "vue";

import {
  formatKeyHint,
  isApplePlatform,
  undoKeyHint,
} from "../../utils/shortcutHints";

const props = defineProps({
  kind: { type: String, required: true }, // 'binary' | 'pair'
  direction: { type: String, default: "remove" },
  canUndo: { type: Boolean, default: false },
  gamify: { type: Boolean, default: false },
  // Key-slip guard: right after the card TYPE changes, decisions are briefly
  // disabled so a rapid-keyed N can't fire "Neither" unseen. This is the ONLY
  // real `disabled` on a decision button - it lasts 300ms and has nothing to
  // explain, so losing tab order for that moment costs nothing.
  hold: { type: Boolean, default: false },
  // Per-decision block reasons: { yes|no|both|neither|left|right|undo: reason }.
  // A non-empty reason marks that control `aria-disabled` (still focusable) and
  // points its aria-describedby at the lock chip. The parent owns the guard, so
  // clicking a blocked control still emits and is stopped in ONE place shared
  // with the keyboard path.
  blocked: { type: Object, default: () => ({}) },
  // Persistent lock chip: `lockNote` is its label (chip hidden when empty),
  // `lockDetail` its tooltip. Copy comes from lockedSetCopy.js.
  lockNote: { type: String, default: "" },
  lockDetail: { type: String, default: "" },
  // Bumped by the parent when a blocked control is pressed - flashes the chip so
  // the (usually sighted) keyboard user gets a visible answer as well as the
  // announced one.
  flashTick: { type: Number, default: 0 },
});

const emit = defineEmits(["answer", "corner", "skip", "undo", "gamify-toggle"]);

const chipId = useId();

const undoBlocked = computed(() =>
  props.canUndo ? props.blocked?.undo || "" : "",
);

// The control teaches BOTH keys, and says which stack it undoes. Ctrl+Z is the
// app-wide vocabulary and now works here too, but it is the review's own
// single-step undo that it runs, not the app-wide history behind the overlay.
const undoTitle = computed(
  () =>
    `Undo the last decision in this review (U or ${formatKeyHint(undoKeyHint())}). Reopens it and reverses the tag change.`,
);
// A space-separated list of alternatives, which is what the attribute takes.
const undoKeyShortcuts = computed(() =>
  isApplePlatform() ? "Meta+Z U" : "Control+Z U",
);

// aria-disabled (NOT disabled) + the reason, co-located via aria-describedby so
// it is heard on focus rather than only on a hover the keyboard never performs.
function lockAttrs(key) {
  const reason = key === "undo" ? undoBlocked.value : props.blocked?.[key] || "";
  if (!reason) return {};
  return {
    "aria-disabled": "true",
    "aria-describedby": chipId,
    title: reason,
  };
}

const flashing = ref(false);
let flashTimer = null;

watch(
  () => props.flashTick,
  (tick) => {
    if (!tick) return;
    flashing.value = false;
    if (flashTimer) clearTimeout(flashTimer);
    // Re-trigger the animation on a repeat press of the same blocked control.
    requestAnimationFrame(() => {
      flashing.value = true;
      flashTimer = setTimeout(() => {
        flashing.value = false;
      }, 200); // --dur-2
    });
  },
);

onUnmounted(() => {
  if (flashTimer) clearTimeout(flashTimer);
});
</script>

<style scoped>
.rs-decide {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  flex-wrap: nowrap;
  min-height: 44px;
}

.rs-decide-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 36px;
  padding: 0 14px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  white-space: nowrap;
  transition: background 0.12s;
}
.rs-decide-btn:hover:not(:disabled):not([aria-disabled="true"]) {
  background: rgba(var(--v-theme-on-dark-surface), 0.14);
}
/* Same treatment for both: `disabled` (the 300ms key-slip hold) and
   `aria-disabled` (a lock, which stays focusable so its reason is reachable). */
.rs-decide-btn:disabled,
.rs-decide-btn[aria-disabled="true"] {
  opacity: 0.45;
  cursor: not-allowed;
}

/* Lock note, anchored left so the decision buttons stay right. It is the
   aria-describedby target of every control it explains, so it must stay in the
   DOM (and visible) for as long as any control is blocked. */
.rs-decide-lock {
  margin-right: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
}
.rs-decide-lock .v-icon {
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
}
/* Visible answer to a blocked press - the announcement's sighted counterpart. */
.rs-decide-lock--flash {
  animation: rs-lock-flash var(--dur-2) var(--ease-standard);
}
@keyframes rs-lock-flash {
  50% {
    background: color-mix(
      in srgb,
      rgb(var(--v-theme-warning)) 26%,
      transparent
    );
    color: rgb(var(--v-theme-warning));
  }
}
@media (prefers-reduced-motion: reduce) {
  .rs-decide-lock--flash {
    animation: none;
    background: color-mix(
      in srgb,
      rgb(var(--v-theme-warning)) 26%,
      transparent
    );
    color: rgb(var(--v-theme-warning));
  }
}
.rs-decide-btn kbd {
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  padding: 1px 5px;
  border-radius: 3px;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.3);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
}
.rs-decide-verb {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}
.rs-decide-sub {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  white-space: nowrap;
}

.rs-decide-btn--yes {
  border-color: color-mix(in srgb, rgb(var(--v-theme-primary)) 45%, transparent);
}
.rs-decide-btn--yes .rs-decide-verb {
  color: rgb(var(--v-theme-primary));
}
.rs-decide-btn--yes:hover:not(:disabled) {
  background: color-mix(in srgb, rgb(var(--v-theme-primary)) 12%, transparent);
}
.rs-decide-btn--no {
  border-color: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 45%, transparent);
}
.rs-decide-btn--no .rs-decide-verb {
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-decide-btn--no:hover:not(:disabled) {
  background: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 12%, transparent);
}

.rs-decide-sep {
  width: 1px;
  height: 28px;
  background: rgba(var(--v-theme-on-dark-surface), 0.18);
}
/* The fixed 12px spacer between Undo and the fun toggle (per the mock). */
.rs-decide-gap {
  width: 12px;
  flex-shrink: 0;
}

.rs-gamify {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  user-select: none;
  white-space: nowrap;
}
.rs-gamify--on {
  color: rgb(var(--v-theme-accent));
}
.rs-gamify input {
  width: 15px;
  height: 15px;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}
.rs-gamify-label {
  font-weight: var(--weight-semibold);
}
.rs-gamify-emoji {
  display: inline-block;
  width: 1em;
}
</style>
