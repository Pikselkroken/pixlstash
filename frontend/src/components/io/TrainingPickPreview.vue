<template>
  <AppDialog
    :open="!!pick"
    :title="pick ? `Pick ${pick.rank}` : ''"
    :subtitle="pick?.label || ''"
    size="xl"
    @close="emit('close')"
  >
    <template v-if="pick">
      <div
        class="tpp-stage"
        :class="{ 'tpp-stage--zoomed': zoomed }"
        data-testid="tpp-stage"
      >
        <SampleMedia
          class="tpp-media"
          :src="src"
          :alt="pick.label || `Pick ${pick.rank}`"
          playing
          data-testid="tpp-media"
          @click="zoomed = !zoomed"
          @error="onError"
        />
      </div>
      <p class="tpp-note">
        {{ pick.off ? "Left out of the set." : "In the set." }}
        <template v-if="!isVideo">
          Click the picture to {{ zoomed ? "fit it" : "see it at full size" }}.
        </template>
      </p>
    </template>
    <template #footer>
      <AppButton key-hint="esc" @click="emit('close')">Close</AppButton>
      <AppButton
        :disabled="locked"
        :icon-left="pick?.off ? 'undo' : 'close'"
        data-testid="tpp-toggle"
        @click="emit('toggle', pick.picture_id)"
      >
        {{ pick?.off ? "Put back in the set" : "Leave out of the set" }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * One pick of "Create a training set…" at full size: the picture itself, or
 * the clip playing. The suggestion carries no file format, so the thumbnail
 * shows until the picture's metadata says which file to load, and stays when
 * the browser cannot draw that file (a RAW).
 */
import { computed, nextTick, ref, watch } from "vue";

import {
  getPictureMetadata,
  pictureMediaUrl,
  pictureThumbnailUrl,
} from "../../api/pictures";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import SampleMedia from "../widgets/SampleMedia.vue";

const props = defineProps({
  /** `{ picture_id, rank, label, off }`, or null when closed. */
  pick: { type: Object, default: null },
  /** The set is being made or is made: the pick can no longer change. */
  locked: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "toggle"]);

const src = ref("");
const zoomed = ref(false);
const isVideo = computed(() => /\.mp4(?:$|[?#])/i.test(src.value));

function onError() {
  const thumb = pictureThumbnailUrl(props.pick.picture_id);
  if (src.value !== thumb) src.value = thumb;
}

// Opened from code, so no activator owns the focus: it goes back to the tile.
let returnFocus = null;
watch(
  () => props.pick?.picture_id,
  async (id, previous) => {
    zoomed.value = false;
    if (id == null) {
      await nextTick();
      if (returnFocus?.isConnected) returnFocus.focus();
      returnFocus = null;
      return;
    }
    if (previous == null) {
      returnFocus =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
    }
    src.value = pictureThumbnailUrl(id);
    try {
      const meta = await getPictureMetadata(id);
      if (props.pick?.picture_id === id && meta?.format) {
        src.value = pictureMediaUrl(id, meta.format);
      }
    } catch (e) {
      console.warn("Couldn't read picture", id, "for its preview:", e);
    }
  },
);
</script>

<style scoped>
.tpp-stage {
  display: flex;
  justify-content: center;
  overflow: auto;
  max-height: 70vh;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
}

.tpp-media {
  display: block;
  max-width: 100%;
  max-height: 70vh;
  object-fit: contain;
  cursor: zoom-in;
}

/* Full size: the stage scrolls. */
.tpp-stage--zoomed {
  justify-content: flex-start;
}

.tpp-stage--zoomed .tpp-media {
  max-width: none;
  max-height: none;
  cursor: zoom-out;
}

.tpp-note {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
