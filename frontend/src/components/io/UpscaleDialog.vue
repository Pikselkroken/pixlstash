<template>
  <!-- Upscale… (#1868): the selection pill's and the lightbox's verb, for the
       keepers picked by eye after a run. The same nodes and the same
       remembered factor and model as the Run popup's added upscale; each
       result is stacked on top of its picture. Mounted once, in App.vue. -->
  <AppDialog
    :open="open"
    :title="title"
    :subtitle="sizeText"
    size="sm"
    @close="upscale.close()"
    @accept="submit"
  >
    <div class="upd-thumbs" data-testid="upscale-thumbs">
      <img
        v-for="id in shownIds"
        :key="id"
        class="upd-thumb"
        :src="pictureThumbnailUrl(id)"
        alt=""
      />
      <span v-if="moreCount" class="upd-thumb upd-more">+{{ moreCount }}</span>
    </div>
    <UpscaleChoice :disabled="sending" />
    <p v-if="offerRealEsrgan" class="upd-get" data-testid="upscale-get-model">
      <template v-if="realEsrganBusy"
        >Downloading RealESRGAN: the Tasks tab shows how far.</template
      >
      <button v-else type="button" class="upd-link" @click="getRealEsrgan">
        Download RealESRGAN x4, a general-purpose upscaler
      </button>
    </p>
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
import { pictureThumbnailUrl } from "../../api/pictures";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { preflightUpscale, upscalePictures } from "../../api/workflows";
import { downloadModels } from "../../composables/useModelDownloads";
import { errorDetail } from "../../utils/apiError";
import { isReadOnly } from "../../utils/apiClient";

// Offered when the shelf has no RealESRGAN: by this name the server finds its
// link in the committed template list (POST /model-downloads), and its
// tensors file it as an upscaler wherever it lands (`has_upscaler_markers`).
const REALESRGAN_FILE = "RealESRGAN_x4plus.safetensors";
// Five thumbnails fit the sm dialog's width; past that, four and a "+n".
const MAX_THUMBS = 5;

const upscale = useUpscaleStore();
const notices = useNoticeStore();
const tasks = useTasksStore();
const runDialog = useRunDialogStore();

const open = computed(() => upscale.pictureIds !== null);
const count = computed(() => upscale.pictureIds?.length || 0);
const title = computed(() =>
  count.value === 1 ? "Upscale picture" : `Upscale ${count.value} pictures`,
);

const shownIds = computed(() => {
  const ids = upscale.pictureIds || [];
  return ids.length > MAX_THUMBS ? ids.slice(0, MAX_THUMBS - 1) : ids;
});
const moreCount = computed(() => count.value - shownIds.value.length);

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

/** "→ 2048 × 2048" beside the title, once the size is known. */
const sizeText = computed(() => {
  if (!size.value) return "";
  const { width, height } = size.value;
  const by = upscale.factor;
  return `→ ${Math.round(width * by)} × ${Math.round(height * by)}`;
});

const realEsrgan = computed(() =>
  (upscale.models || []).find((m) =>
    /realesrgan/i.test(`${m.name} ${m.filename || ""}`),
  ),
);
const offerRealEsrgan = computed(
  () => !isReadOnly.value && upscale.models !== null && !realEsrgan.value,
);
const starting = ref(false);
// Set by the link, so the model it fetched is the one picked once it lands.
const pickRealEsrgan = ref(false);
const realEsrganBusy = computed(
  () => starting.value || tasks.runningDownloadFiles.has(REALESRGAN_FILE),
);

async function getRealEsrgan() {
  starting.value = true;
  try {
    pickRealEsrgan.value = (await downloadModels([REALESRGAN_FILE])) > 0;
  } finally {
    starting.value = false;
  }
}

// A landed download may be an upscaler: read the shelf again.
watch(
  () => tasks.downloadsLanded,
  () => upscale.loadModels(),
);

watch(realEsrgan, (found) => {
  if (found && pickRealEsrgan.value) {
    pickRealEsrgan.value = false;
    upscale.setModel(found.sha256);
  }
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
    // Followed as a Run popup's runs are: the Tasks tab, and the lightbox,
    // which opens one picture's result over it, before / after.
    tasks.nudge();
    runDialog.started(Array.isArray(answer?.prompts) ? answer.prompts : [], ids);
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
.upd-thumbs {
  display: flex;
  gap: var(--space-2);
}

.upd-thumb {
  width: 64px;
  height: 64px;
  border-radius: var(--radius-sm);
  object-fit: cover;
  display: block;
}

.upd-more {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
  box-sizing: border-box;
  border: 1px solid rgb(var(--v-theme-border));
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.upd-get {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.upd-link {
  background: none;
  border: none;
  padding: 0;
  color: inherit;
  text-decoration: underline;
  cursor: pointer;
  font: inherit;
}

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
}
</style>
