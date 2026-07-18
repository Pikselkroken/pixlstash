<template>
  <!-- One shared, right-anchored decision bar for BOTH card types. Constant
       button slots per kind, Undo ALWAYS rendered (disabled when the history
       is empty), no wrapping — the buttons never move mid-loop. -->
  <div class="rs-decide" role="toolbar" aria-label="Decision">
    <!-- Locked suspect (a pre-lock open session can still hold one): decisions
         write the frozen label ledger, so they're disabled here rather than
         letting the click hit the backend and surface a raw 423. Skip/Undo stay
         live so the reviewer can move past the card. -->
    <span v-if="locked" class="rs-decide-lock" :title="lockReason">
      <v-icon size="15">mdi-lock-outline</v-icon>
      <span>Locked — Skip to move on</span>
    </span>

    <template v-if="kind === 'binary'">
      <button
        class="rs-decide-btn rs-decide-btn--yes"
        type="button"
        :disabled="hold || locked"
        :title="locked ? lockReason : undefined"
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
        :disabled="hold || locked"
        :title="locked ? lockReason : undefined"
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
        :disabled="hold || locked"
        :title="locked ? lockReason : undefined"
        @click="emit('corner', 'both')"
      >
        <kbd>B</kbd>
        <span class="rs-decide-verb">Both</span>
        <span class="rs-decide-sub">tag both versions</span>
      </button>
      <button
        class="rs-decide-btn rs-decide-btn--no"
        type="button"
        :disabled="hold || locked"
        :title="locked ? lockReason : undefined"
        @click="emit('corner', 'neither')"
      >
        <kbd>N</kbd>
        <span class="rs-decide-verb">Neither</span>
        <span class="rs-decide-sub">clear the tag</span>
      </button>
      <button
        class="rs-decide-btn"
        type="button"
        :disabled="hold || locked"
        :title="locked ? lockReason : undefined"
        @click="emit('corner', 'left')"
      >
        <kbd>L</kbd>
        <span class="rs-decide-verb">Left only</span>
        <span class="rs-decide-sub">keep as is</span>
      </button>
      <button
        class="rs-decide-btn"
        type="button"
        :disabled="hold || locked"
        :title="locked ? lockReason : undefined"
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
      title="Can't decide — the card leaves the queue with no change made. Undo brings it back."
      @click="emit('skip')"
    >
      <kbd>S</kbd>
      <span class="rs-decide-verb">Skip</span>
    </button>
    <button
      class="rs-decide-btn"
      type="button"
      :disabled="!canUndo"
      title="Undo the last decision — reopens it and reverses the tag change."
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
defineProps({
  kind: { type: String, required: true }, // 'binary' | 'pair'
  direction: { type: String, default: "remove" },
  canUndo: { type: Boolean, default: false },
  gamify: { type: Boolean, default: false },
  // Key-slip guard: right after the card TYPE changes, decisions are briefly
  // disabled so a rapid-keyed N can't fire "Neither" unseen.
  hold: { type: Boolean, default: false },
  // Suspect picture is in a locked set: disable the decision buttons (Skip/Undo
  // stay live). `lockReason` is the tooltip explaining why / how to unlock.
  locked: { type: Boolean, default: false },
  lockReason: { type: String, default: "" },
});

const emit = defineEmits(["answer", "corner", "skip", "undo", "gamify-toggle"]);
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
.rs-decide :is(button, input):focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: 1px;
}

.rs-decide-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 36px;
  padding: 0 14px;
  cursor: pointer;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  white-space: nowrap;
  transition: background 0.12s;
}
.rs-decide-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-dark-surface), 0.14);
}
.rs-decide-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* Locked-suspect note, anchored left so the decision buttons stay right. */
.rs-decide-lock {
  margin-right: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
}
.rs-decide-lock .v-icon {
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
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
  border-color: color-mix(in srgb, rgb(var(--v-theme-error)) 45%, transparent);
}
.rs-decide-btn--no .rs-decide-verb {
  color: rgb(var(--v-theme-error));
}
.rs-decide-btn--no:hover:not(:disabled) {
  background: color-mix(in srgb, rgb(var(--v-theme-error)) 12%, transparent);
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
