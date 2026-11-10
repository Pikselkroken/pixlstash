<template>
  <LoraLossChart v-if="curve" :curve="curve" />
</template>

<script setup>
/**
 * The loss chart of the training run a shelf model came from, drawn inside the
 * run's expanded stack. Read by the run's id, the one `model.training_run_id`
 * carries. A run that belongs to another library answers 404, and this draws
 * nothing rather than an error in the shelf.
 */
import { ref, watch } from "vue";
import { getTrainingJob } from "../../api/training";
import LoraLossChart from "../widgets/LoraLossChart.vue";

const props = defineProps({
  runId: { type: Number, default: null },
});

const curve = ref(null);
let latest = 0;

watch(
  () => props.runId,
  async (runId) => {
    const request = ++latest;
    curve.value = null;
    if (runId == null) return;
    try {
      const job = await getTrainingJob(runId);
      if (request === latest) {
        curve.value = Array.isArray(job?.loss_curve) ? job.loss_curve : [];
      }
    } catch (err) {
      if (err?.response?.status !== 404) {
        console.warn(`Could not read the loss of training run ${runId}:`, err);
      }
      if (request === latest) curve.value = null;
    }
  },
  { immediate: true },
);
</script>

