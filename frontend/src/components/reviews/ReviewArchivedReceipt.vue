<template>
  <div class="rs-archived">
    <div class="rs-archived-panel">
      <div class="rs-archived-head">
        <v-icon size="22" class="rs-archived-check">mdi-check-decagram</v-icon>
        <h2 class="rs-archived-title">Archived review: “{{ review.tag }}”</h2>
      </div>

      <div class="rs-archived-grid">
        <span class="rs-archived-label">Scanned</span>
        <span>{{ (review.stats?.scanned ?? 0).toLocaleString() }} pictures</span>
        <span class="rs-archived-label">Suspects found</span>
        <span>{{ review.stats?.found ?? 0 }}</span>
        <span class="rs-archived-label">Handled earlier</span>
        <span>{{ review.stats?.prev_reviewed ?? 0 }}</span>
        <span class="rs-archived-label">Created</span>
        <span>{{ formatWhen(review.created_at) }}</span>
        <template v-if="review.refreshed_at">
          <span class="rs-archived-label">Last refreshed</span>
          <span>{{ formatWhen(review.refreshed_at) }}</span>
        </template>
      </div>

      <p class="rs-archived-tally">
        <span class="rs-archived-removed">✗ {{ receipt.removed }} removed</span>
        <span class="rs-archived-added">+ {{ receipt.added }} added</span>
        <span class="rs-archived-kept">✓ {{ receipt.kept }} kept</span>
      </p>

      <button class="rs-archived-back" type="button" @click="store.showBoard()">
        <v-icon size="15">mdi-arrow-left</v-icon> Back to tag health
      </button>
    </div>
  </div>
</template>

<script setup>
// Read-only receipt for an archived review - the audit trail the health
// board's overturn rate feeds on.
import { computed } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";

const props = defineProps({
  review: { type: Object, required: true },
});

const store = useReviewSessionsStore();

const receipt = computed(() => store.receiptFor(props.review.id));

function formatWhen(iso) {
  if (!iso) return " - ";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
</script>

<style scoped>
.rs-archived {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.rs-archived-panel {
  width: 420px;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-dark-surface), 0.04);
}
.rs-archived-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.rs-archived-check {
  color: rgb(var(--v-theme-dark-surface-success));
}
.rs-archived-title {
  font-size: 16px;
  font-weight: var(--weight-bold);
}
.rs-archived-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 16px;
  font-size: var(--text-sm);
}
.rs-archived-label {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-archived-tally {
  display: flex;
  gap: 12px;
  font-size: var(--text-sm);
}
.rs-archived-removed {
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-archived-added {
  color: rgb(var(--v-theme-dark-surface-primary));
}
.rs-archived-kept {
  color: rgb(var(--v-theme-dark-surface-success));
}
.rs-archived-back {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 11px;
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
</style>
