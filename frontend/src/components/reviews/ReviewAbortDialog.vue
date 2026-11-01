<template>
  <AppDialog :open="showing" :title="`Abort “${target.tag}”?`" size="sm" @close="cancel">
    <p class="rs-abort-msg">
      You made {{ target.changes }} change{{ target.changes === 1 ? "" : "s" }}
      in this review.
    </p>

    <template #footer>
      <AppButton
        variant="primary"
        tooltip="Abort the review; the changes stand"
        @click="keep"
      >
        Keep {{ target.changes }} change{{ target.changes === 1 ? "" : "s" }}
      </AppButton>
      <AppButton
        variant="danger"
        tooltip="Reverse every change this review made, then abort"
        @click="undo"
      >
        Undo {{ target.changes }} change{{ target.changes === 1 ? "" : "s" }}
      </AppButton>
      <AppButton ref="cancelRef" variant="secondary" @click="cancel">
        Cancel
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
import { nextTick, ref } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";

// `closed` fires once the dialog has left the screen, after any store call, so
// the opener can put focus back on a row that still exists (or not).
const emit = defineEmits(["closed"]);
const store = useReviewSessionsStore();

// The review being asked about. It stays after the dialog closes, so the
// leave transition still has its words to draw.
const target = ref({ id: null, tag: "", changes: 0 });
const showing = ref(false);
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
  showing.value = true;
  await nextTick();
  cancelRef.value?.$el?.focus();
}

function cancel() {
  showing.value = false;
  emit("closed");
}

// Keep / Undo wait for the abort to land before `closed`, so the opener sees
// whether the row it came from still exists.
async function keep() {
  const id = target.value.id;
  showing.value = false;
  await store.abortSession(id);
  emit("closed");
}

async function undo() {
  const id = target.value.id;
  showing.value = false;
  await store.undoChangesAndAbort(id);
  emit("closed");
}

defineExpose({ open });
</script>

<style scoped>
/* The dialog, its focus trap and Esc come from AppDialog (v-dialog). */
.rs-abort-msg {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
