<template>
  <!-- Settings › Libraries & imports, third section (#2807): the playable
       copies of videos this browser can't play, which live in the open
       library's folder. Only there when the library has some. Never gives way
       to the lists above it. -->
  <SettingsSection v-if="summary?.count" title="Video copies">
    <template #action>
      <AppButton
        size="sm"
        variant="secondary"
        icon-left="delete"
        :loading="deleting"
        @click="removeAll"
      >
        Delete copies
      </AppButton>
    </template>
    <p class="vc-line">
      <b class="vc-total num"
        >{{ summary.count.toLocaleString() }}
        {{ summary.count === 1 ? "copy" : "copies" }} ·
        {{ formatModelSize(summary.bytes) }}</b
      >
      <span
        >Playable copies of videos this browser can't play. A deleted copy is
        made again when its clip is next opened.</span
      >
    </p>
  </SettingsSection>
</template>

<script setup>
import { nextTick, ref, watch } from "vue";

import { deleteVideoCopies, fetchVideoCopiesSummary } from "../../api/libraries";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";
import { formatModelSize } from "../../utils/modelShelf";
import AppButton from "../widgets/AppButton.vue";
import SettingsSection from "./SettingsSection.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const noticeStore = useNoticeStore();
const summary = ref(null);
const deleting = ref(false);

async function refresh() {
  try {
    summary.value = await fetchVideoCopiesSummary();
  } catch (err) {
    // Nothing to show is the honest answer when it cannot be read.
    console.warn("Could not read the library's video copies", err);
    summary.value = null;
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) refresh();
  },
  { immediate: true },
);

// No confirmation: a copy is a cache, made again when its clip is opened.
async function removeAll() {
  deleting.value = true;
  try {
    const { deleted, bytes } = await deleteVideoCopies();
    noticeStore.success(
      `Deleted ${deleted} video ${deleted === 1 ? "copy" : "copies"} (${formatModelSize(bytes)}).`,
      { key: "video-copies-deleted" },
    );
  } catch (err) {
    console.error("Failed to delete the library's video copies", err);
    noticeStore.error(
      `Couldn't delete the video copies. ${errorDetail(err) || ""}`.trim(),
      { key: "video-copies-delete" },
    );
  } finally {
    deleting.value = false;
  }
  await refresh();
  // The section goes with its last copy; keep the keyboard on this pane's
  // own entry in the rail rather than dropping it to the page.
  if (!summary.value?.count) {
    await nextTick();
    document.getElementById("settings-nav-libraries")?.focus();
  }
}
</script>

<style scoped>
.vc-line {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.vc-total {
  flex: none;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-surface));
  white-space: nowrap;
}
</style>
