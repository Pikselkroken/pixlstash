<template>
  <div ref="rootRef" class="inbox-bar">
    <div class="inbox-bar__head">
      <span class="inbox-bar__count">{{ countLabel }}</span>
      <AppButton
        v-if="folder"
        size="sm"
        variant="ghost"
        icon-left="close"
        @click="emit('narrow', null)"
      >
        All folders
      </AppButton>
      <span class="inbox-bar__spacer"></span>
      <!-- Reports what each folder is doing and edits nothing: folders are
           added and changed in one place, Settings › Import folders. -->
      <v-menu location="bottom end" origin="top end" :offset="6">
        <template #activator="{ props: menuProps }">
          <AppButton
            v-bind="menuProps"
            size="sm"
            variant="secondary"
            icon-left="folder-download-outline"
            aria-haspopup="menu"
          >
            Import folders
          </AppButton>
        </template>
        <div class="ctx-menu" role="menu" tabindex="-1" @keydown="onMenuKeydown">
          <div
            v-for="entry in store.folders"
            :key="entry.id"
            class="inbox-status"
            role="none"
          >
            <span class="inbox-status__name">{{
              entry.label || entry.folder
            }}</span>
            <span
              :class="[
                'inbox-status__state',
                { 'inbox-status__state--missing': entry.missing },
              ]"
              >{{ folderStatus(entry) }}</span
            >
          </div>
          <button
            class="ctx-item"
            type="button"
            role="menuitem"
            @click="emit('manage')"
          >
            <v-icon class="ctx-icon">mdi-cog-outline</v-icon>
            <span class="ctx-label-text">Manage import folders…</span>
          </button>
        </div>
      </v-menu>
      <AppButton
        size="sm"
        variant="secondary"
        :disabled="!visibleBatches.length || busy"
        @click="markAll"
      >
        {{ folder ? "Mark folder seen" : "Mark all seen" }}
      </AppButton>
    </div>
    <p class="inbox-bar__hint">
      New pictures from your import folders wait here. Rating, tagging, filing
      or opening one takes it out of the Inbox; it stays in All media either
      way.
    </p>
    <ul v-if="visibleBatches.length" class="inbox-bar__batches" role="list">
      <li
        v-for="batch in visibleBatches"
        :key="`${batch.import_source_folder}|${batch.first_imported_at}`"
        class="inbox-batch"
      >
        <button
          type="button"
          class="inbox-batch__name"
          :aria-pressed="folder === batch.import_source_folder"
          @click="emit('narrow', batch.import_source_folder)"
        >
          {{ batch.label }}
        </button>
        <span class="inbox-batch__meta"
          >{{ batch.count.toLocaleString() }} new ·
          {{ batchWhen(batch) }}</span
        >
        <AppButton
          size="sm"
          variant="ghost"
          :disabled="busy"
          @click="markBatch(batch)"
        >
          Mark seen
        </AppButton>
      </li>
    </ul>
  </div>
</template>

<script setup>
/**
 * The Inbox's band over the grid: how many pictures import folders brought in
 * that have not been looked at, one row per batch (a folder's arrivals with no
 * hour-long gap), Mark seen per batch and for everything, and a status menu
 * whose last row opens Settings › Import folders.
 *
 * The grid underneath is the ordinary grid narrowed to the Inbox, so opening,
 * rating, tagging and filing work as everywhere else. The parent re-reads it
 * after a Mark seen (`marked`), and sizes itself to this band (`resize`).
 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import AppButton from "./AppButton.vue";
import { useImportFoldersStore } from "../../stores/useImportFoldersStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { relativeDate } from "../../utils/snapshots";

const props = defineProps({
  // The import folder the Inbox is narrowed to, or null for all of them.
  folder: { type: String, default: null },
});
const emit = defineEmits(["narrow", "manage", "marked", "resize"]);

const store = useImportFoldersStore();
const noticeStore = useNoticeStore();
const busy = ref(false);
const rootRef = ref(null);

const visibleBatches = computed(() =>
  store.inbox.batches.filter(
    (batch) => !props.folder || batch.import_source_folder === props.folder,
  ),
);

const countLabel = computed(() => {
  const total = visibleBatches.value.reduce((sum, b) => sum + b.count, 0);
  if (!total) return "Nothing new";
  if (props.folder) return `${total.toLocaleString()} new`;
  const folders = store.inbox.folder_count;
  return `${total.toLocaleString()} new from ${folders} import ${
    folders === 1 ? "folder" : "folders"
  }`;
});

function folderStatus(entry) {
  if (entry.missing) return "Folder not found";
  if (!entry.last_imported_at) return "Nothing imported yet";
  return `Last new picture ${relativeDate(entry.last_imported_at)}`;
}

function dayWord(date) {
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const diffDays = Math.round(
    (startOfToday - new Date(date).setHours(0, 0, 0, 0)) / 86400000,
  );
  if (diffDays === 0) return "today";
  if (diffDays === 1) return "yesterday";
  return date.toLocaleDateString();
}

function clock(date) {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** "today 14:02 – 14:20", or one time for a batch that landed in a minute. */
function batchWhen(batch) {
  const first = new Date(batch.first_imported_at);
  const last = new Date(batch.last_imported_at);
  const start = clock(first);
  const end = clock(last);
  return start === end
    ? `${dayWord(last)} ${end}`
    : `${dayWord(first)} ${start} – ${end}`;
}

async function run(action, what) {
  busy.value = true;
  try {
    await action();
    emit("marked");
  } catch (err) {
    console.error(`Could not mark ${what} seen`, err);
    noticeStore.error(
      `Couldn't mark ${what} seen. ${errorDetail(err) || ""}`.trim(),
      { key: "inbox-mark-seen" },
    );
  } finally {
    busy.value = false;
  }
}

function markBatch(batch) {
  return run(() => store.markBatchSeen(batch), "that batch");
}

function markAll() {
  return run(
    () =>
      store.markSeen(
        props.folder ? { import_source_folder: props.folder } : { all: true },
      ),
    props.folder ? "that folder" : "the Inbox",
  );
}

// The grid hangs below this band, whose height follows the batches it lists.
let observer = null;
onMounted(() => {
  store.refresh();
  if (typeof ResizeObserver === "undefined" || !rootRef.value) return;
  observer = new ResizeObserver(() =>
    emit("resize", rootRef.value?.offsetHeight ?? 0),
  );
  observer.observe(rootRef.value);
});
onBeforeUnmount(() => {
  observer?.disconnect();
  emit("resize", 0);
});
</script>

<style scoped>
.inbox-bar {
  position: absolute;
  left: 0;
  top: calc(var(--selbar-height, 48px) + var(--filter-strip-h, 0px));
  width: 100%;
  z-index: var(--z-sticky);
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: rgb(var(--v-theme-background));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.inbox-bar__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.inbox-bar__count {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.inbox-bar__spacer {
  flex: 1;
}

.inbox-bar__hint {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* A handful of batches stays in view; a long backlog scrolls in place rather
   than pushing the grid off the screen. */
.inbox-bar__batches {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: calc(4 * 32px);
  overflow-y: auto;
}

.inbox-batch {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: 32px;
}

.inbox-batch__name {
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-background));
  transition: background var(--dur-1) var(--ease-standard);
}

.inbox-batch__name:hover {
  background: var(--hover-wash);
}

.inbox-batch__name[aria-pressed="true"] {
  background: var(--active-wash);
  box-shadow: inset 2px 0 0 var(--active-bar);
}

.inbox-batch__meta {
  flex: 1;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.7);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* A status line in the menu, not a control: it reports and does nothing. */
.inbox-status {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.inbox-status__name {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.inbox-status__state {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.inbox-status__state--missing {
  color: rgb(var(--v-theme-surface-error));
}
</style>
