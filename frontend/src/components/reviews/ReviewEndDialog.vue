<template>
  <!-- md, not sm: three verbs with their counts do not fit 420px. -->
  <AppDialog :open="showing" :title="`End “${target.tag}”`" @close="cancel">
    <p class="rs-end-msg">
      You decided {{ target.changes }}
      <template v-if="target.found">of {{ target.found }}</template>
      card{{ (target.found || target.changes) === 1 ? "" : "s" }}.
      <template v-if="target.found > target.changes">
        The other {{ target.found - target.changes }} stay as they are.
      </template>
    </p>

    <!-- Cancel, then the danger verb, then the key action on the right: the
         design system's footer order. Focus opens on Cancel. -->
    <template #footer>
      <AppButton ref="cancelRef" variant="secondary" @click="cancel">
        Cancel
      </AppButton>
      <AppButton
        variant="danger"
        icon-left="undo"
        tooltip="Reverse every change this review made, then end it"
        @click="undo"
      >
        Undo {{ changesLabel }}
      </AppButton>
      <AppButton
        variant="primary"
        icon-left="check"
        tooltip="End the review; the changes stand and its receipt goes under Archived"
        @click="keep"
      >
        Keep {{ changesLabel }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
import { computed, nextTick, ref } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";

// The one way to end an open review from a menu ("End review…", the sidebar
// row and the Reviews inspector's Open tab). Keep files the receipt under
// Archived (store.archiveSession); Undo reverses the changes and leaves no
// receipt (store.undoChangesAndAbort).
//
// `closed` fires once the dialog has left the screen, after any store call, so
// the opener can put focus back on a row that still exists (or not).
const emit = defineEmits(["closed"]);
const store = useReviewSessionsStore();

// The review being asked about. It stays after the dialog closes, so the
// leave transition still has its words to draw.
const target = ref({ id: null, tag: "", changes: 0, found: 0 });
const showing = ref(false);
const cancelRef = ref(null);

const changesLabel = computed(
  () =>
    `${target.value.changes} change${target.value.changes === 1 ? "" : "s"}`,
);

// Zero decided changes means nothing to keep or undo, so the review ends at
// once with no dialog and no receipt. Otherwise the dialog asks Keep / Undo /
// Cancel, and focus lands on Cancel so Enter on a stray key never discards
// anything.
async function open(id) {
  const s = store.sessions.find((x) => x.id === id);
  const changes = store.decidedCountFor(id);
  if (!changes) {
    await store.abortSession(id);
    emit("closed");
    return;
  }
  target.value = { id, tag: s?.tag ?? "", changes, found: s?.stats?.found ?? 0 };
  showing.value = true;
  await nextTick();
  cancelRef.value?.$el?.focus();
}

function cancel() {
  showing.value = false;
  emit("closed");
}

// Keep / Undo wait for the store call to land before `closed`, so the opener
// sees whether the row it came from still exists.
async function keep() {
  const id = target.value.id;
  showing.value = false;
  await store.archiveSession(id);
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
.rs-end-msg {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
