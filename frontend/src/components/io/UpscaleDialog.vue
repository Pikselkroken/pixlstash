<template>
  <!-- Upscale… (#1868): the selection pill's and the lightbox's verb, for the
       keepers picked by eye after a run. The same nodes and the same
       remembered factor and model as the Run popup's added upscale; each
       result is stacked on top of its picture. Mounted once, in App.vue. -->
  <AppDialog
    :open="open"
    :title="title"
    size="sm"
    @close="upscale.close()"
    @accept="submit"
  >
    <UpscaleChoice :size="size" :disabled="sending" />
    <p
      v-if="again.length"
      class="upd-warn"
      role="status"
      data-testid="upscale-again"
    >
      <v-icon size="16" class="upd-icon">mdi-alert-outline</v-icon>
      {{ againText }}
    </p>
    <template #footer>
      <AppButton :disabled="sending" @click="upscale.close()">Cancel</AppButton>
      <AppButton
        variant="primary"
        icon-left="arrow-expand-all"
        :loading="sending"
        data-testid="upscale-submit"
        @click="submit"
        >Upscale {{ count > 1 ? count : "" }}</AppButton
      >
    </template>
  </AppDialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import UpscaleChoice from "./UpscaleChoice.vue";
import { useUpscaleStore } from "../../stores/useUpscaleStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { preflightUpscale, upscalePictures } from "../../api/workflows";
import { errorDetail } from "../../utils/apiError";

const upscale = useUpscaleStore();
const notices = useNoticeStore();
const tasks = useTasksStore();

const open = computed(() => upscale.pictureIds !== null);
const count = computed(() => upscale.pictureIds?.length || 0);
const title = computed(() =>
  count.value === 1 ? "Upscale picture" : `Upscale ${count.value} pictures`,
);

/** The preflight's `{picture_id, width, height, upscaled}` per picture. */
const pictures = ref([]);
const sending = ref(false);

watch(
  () => upscale.pictureIds,
  async (ids) => {
    pictures.value = [];
    if (!ids) return;
    try {
      const answer = await preflightUpscale(ids);
      // Answered for a dialog since closed or reopened on others.
      if (upscale.pictureIds === ids) pictures.value = answer?.pictures || [];
    } catch (err) {
      // Only the sizes and the warning are lost; the upscale itself still runs.
      console.warn("Could not read the pictures to upscale:", err);
    }
  },
  { immediate: true },
);

/** "1024 × 1024 → 2048 × 2048" where every picture is one size. */
const size = computed(() => {
  const sizes = new Set(pictures.value.map((p) => `${p.width}x${p.height}`));
  const [first] = pictures.value;
  return sizes.size === 1 && first?.width && first?.height
    ? { width: first.width, height: first.height }
    : null;
});

/** The pictures already upscaled in their own run: not skipped, but said. */
const again = computed(() => pictures.value.filter((p) => p.upscaled));

const againText = computed(() => {
  const list = again.value;
  const lead =
    list.length === 1 && count.value === 1
      ? "This picture was already upscaled in its run"
      : `${list.length} of these ${list.length === 1 ? "was" : "were"} already upscaled in ${list.length === 1 ? "its" : "their"} run`;
  const [first] = list;
  if (list.length === 1 && first.width && first.height) {
    const by = upscale.factor;
    return `${lead} and would go to ${Math.round(first.width * by)} × ${Math.round(first.height * by)}.`;
  }
  return `${lead}.`;
});

async function submit() {
  const ids = upscale.pictureIds;
  if (!ids || sending.value) return;
  sending.value = true;
  try {
    const answer = await upscalePictures(ids, upscale.body());
    upscale.close();
    // The runs are followed in the Tasks tab, as a Run popup's are.
    tasks.nudge();
    if (answer?.status === "partial") {
      notices.error(
        `Upscaled ${answer.runs} of ${ids.length} pictures. ${answer.error || ""}`,
        { key: "upscale" },
      );
    } else {
      notices.success(
        ids.length === 1
          ? "Started 1 upscale in ComfyUI."
          : `Started ${ids.length} upscales in ComfyUI.`,
      );
    }
  } catch (err) {
    notices.error(`Couldn't upscale. ${errorDetail(err)}`, { key: "upscale" });
  } finally {
    sending.value = false;
  }
}
</script>

<style scoped>
.upd-warn {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-warning));
}

.upd-icon {
  flex: none;
  margin-top: 1px;
}
</style>
