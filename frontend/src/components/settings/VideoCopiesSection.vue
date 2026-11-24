<template>
  <!-- The open library's playable copies of videos (#2807). Shown only when
       there are copies to delete; a deleted copy is made again when its clip
       is next opened. -->
  <section v-if="copies.count > 0" class="vcs" aria-labelledby="vcs-title">
    <div class="vcs-head">
      <h3 id="vcs-title" class="vcs-title section-label">Video copies</h3>
      <AppButton
        class="vcs-delete"
        size="sm"
        variant="secondary"
        :disabled="busy"
        @click="openConfirm"
      >
        Delete copies
      </AppButton>
    </div>
    <p class="vcs-line">
      {{ copyCountLabel }} · {{ humanBytes(copies.bytes) }}
    </p>
    <p class="vcs-note">
      Playable copies of videos this browser can't play. A deleted copy is made
      again when its clip is next opened.
    </p>

    <!-- Deletes bytes: Cancel is the default, and Enter does not accept. -->
    <AppDialog
      :open="confirmOpen"
      title="Delete video copies?"
      size="sm"
      @close="confirmOpen = false"
    >
      <p class="vcs-note">
        {{ copyCountLabel }} ({{ humanBytes(copies.bytes) }}) will be deleted
        from this library. Each is made again when its clip is next opened.
      </p>
      <p v-if="deleteError" class="vcs-error" role="alert">
        {{ deleteError }}
      </p>
      <template #footer>
        <AppButton variant="secondary" @click="confirmOpen = false">
          Cancel
        </AppButton>
        <AppButton variant="danger" :disabled="busy" @click="removeCopies">
          Delete copies
        </AppButton>
      </template>
    </AppDialog>
  </section>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import { deleteVideoCopies, getVideoCopies } from "../../api/libraries";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const copies = ref({ count: 0, bytes: 0 });
const confirmOpen = ref(false);
const deleteError = ref(null);
const busy = ref(false);

const copyCountLabel = computed(() =>
  copies.value.count === 1 ? "1 copy" : `${copies.value.count} copies`,
);

// "0 B", "512 KB", "1.5 GB": the scale the settings rows use elsewhere.
function humanBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let scaled = value;
  let unit = 0;
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  return `${scaled.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

async function refresh() {
  try {
    copies.value = await getVideoCopies();
  } catch (err) {
    console.warn("Could not read the video copies of this library", err);
    copies.value = { count: 0, bytes: 0 };
  }
}

function openConfirm() {
  deleteError.value = null;
  confirmOpen.value = true;
}

async function removeCopies() {
  busy.value = true;
  deleteError.value = null;
  try {
    await deleteVideoCopies();
    confirmOpen.value = false;
    copies.value = { count: 0, bytes: 0 };
  } catch (err) {
    console.warn("Could not delete the video copies of this library", err);
    deleteError.value = "The copies could not be deleted. Try again.";
  } finally {
    busy.value = false;
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) refresh();
  },
  { immediate: true },
);
</script>

<style scoped>
.vcs {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  border-top: 1px solid rgb(var(--v-theme-divider));
  padding-top: var(--space-3);
}

.vcs-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

/* The global `.section-label` sets the type; this only resets the h3. */
.vcs-title {
  margin: 0;
}

.vcs-error {
  margin: var(--space-2) 0 0;
  color: rgb(var(--v-theme-surface-error));
  font-size: var(--text-xs);
}

.vcs-delete {
  margin-left: auto;
}

.vcs-line {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-bold);
}

.vcs-note {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
