<template>
  <!-- Teleported to the body: the sidebar's ancestors can carry transforms that
       would trap a position:fixed backdrop. -->
  <Teleport to="body">
    <div
      v-if="target"
      class="rs-abort-backdrop"
      @click.self="cancel"
      @keydown.esc.stop.prevent="cancel"
      @keydown.tab="trapTab"
    >
      <div
        ref="dialogRef"
        class="rs-abort"
        role="dialog"
        aria-modal="true"
        aria-label="Abort review"
      >
        <h3 class="rs-abort-title">Abort “{{ target.tag }}”?</h3>
        <p class="rs-abort-msg">
          You made {{ target.changes }} change{{
            target.changes === 1 ? "" : "s"
          }}
          in this review.
        </p>
        <div class="rs-abort-actions">
          <button
            class="rs-abort-btn rs-abort-btn--keep"
            type="button"
            @click="keep"
          >
            <Tooltip
              text="Abort the review; the changes stand"
              activator="parent"
            />
            Keep {{ target.changes }} change{{
              target.changes === 1 ? "" : "s"
            }}
          </button>
          <button
            class="rs-abort-btn rs-abort-btn--undo"
            type="button"
            @click="undo"
          >
            <Tooltip
              text="Reverse every change this review made, then abort"
              activator="parent"
            />
            Undo {{ target.changes }} change{{
              target.changes === 1 ? "" : "s"
            }}
          </button>
          <button
            ref="cancelRef"
            class="rs-abort-btn"
            type="button"
            @click="cancel"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import Tooltip from "../widgets/Tooltip.vue";

// `closed` fires once the dialog has left the screen, after any store call, so
// the opener can put focus back on a row that still exists (or not).
const emit = defineEmits(["closed"]);
const store = useReviewSessionsStore();

const target = ref(null); // { id, tag, changes } | null
const dialogRef = ref(null);
const cancelRef = ref(null);

// Zero decided changes means nothing to keep or undo, so the review aborts at
// once with no dialog. Otherwise the dialog asks Keep / Undo / Cancel, and
// focus lands on Cancel so Enter on a stray key never discards anything.
async function open(id) {
  const s = store.sessions.find((x) => x.id === id);
  const changes = store.decidedCountFor(id);
  if (!changes) {
    await store.abortSession(id);
    emit("closed");
    return;
  }
  target.value = { id, tag: s?.tag ?? "", changes };
  await nextTick();
  cancelRef.value?.focus();
}

function cancel() {
  target.value = null;
  emit("closed");
}

// Keep / Undo wait for the abort to land before `closed`, so the opener sees
// whether the row it came from still exists.
async function keep() {
  const d = target.value;
  target.value = null;
  if (d) await store.abortSession(d.id);
  emit("closed");
}

async function undo() {
  const d = target.value;
  target.value = null;
  if (d) await store.undoChangesAndAbort(d.id);
  emit("closed");
}

// Focus trap. Tab and Shift+Tab wrap between the dialog's own buttons (DOM
// order: Keep, Undo, Cancel). Focus that lands outside the dialog while it is
// up is pulled back to Cancel, the dialog's default control. Esc is already
// handled on the backdrop, so the Tags view's key handler never sees it.
function dialogControls() {
  return [...(dialogRef.value?.querySelectorAll("button") ?? [])];
}

function trapTab(event) {
  const controls = dialogControls();
  if (!controls.length) return;
  const first = controls[0];
  const last = controls[controls.length - 1];
  const active = document.activeElement;
  const inside = controls.includes(active);
  if (event.shiftKey && (!inside || active === first)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (!inside || active === last)) {
    event.preventDefault();
    first.focus();
  }
}

function holdFocus(event) {
  if (!target.value) return;
  if (dialogRef.value?.contains(event.target)) return;
  cancelRef.value?.focus();
}

onMounted(() => document.addEventListener("focusin", holdFocus));
onBeforeUnmount(() => document.removeEventListener("focusin", holdFocus));

defineExpose({ open });
</script>

<style scoped>
.rs-abort-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
}
.rs-abort {
  width: 360px;
  max-width: calc(100vw - 32px);
  padding: 18px;
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.rs-abort-title {
  font-size: 15px;
  font-weight: var(--weight-bold);
}
.rs-abort-msg {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-dark-surface), 0.8);
}
.rs-abort-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--space-3);
}
.rs-abort-btn {
  height: 32px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  white-space: nowrap;
}
.rs-abort-btn--keep {
  border-color: color-mix(in srgb, rgb(var(--v-theme-dark-surface-success)) 60%, transparent);
  color: rgb(var(--v-theme-dark-surface-success));
}
.rs-abort-btn--undo {
  border-color: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 60%, transparent);
  color: rgb(var(--v-theme-dark-surface-error));
}
</style>
