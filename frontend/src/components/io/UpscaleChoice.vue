<template>
  <!-- The factor and model of the one upscale (#1868), drawn the same in the
       Run popup's Stages row and the Upscale… dialog: both read and write the
       one remembered choice in useUpscaleStore. -->
  <div class="upc">
    <div class="upc-row">
      <Segmented
        :model-value="upscale.factor"
        :options="factorOptions"
        :aria-label="`${label} by`"
        @update:model-value="upscale.setFactor"
      />
      <AppSelect
        class="upc-model"
        :model-value="upscale.model?.sha256 ?? ''"
        :options="modelOptions"
        :label="`${label} with`"
        hide-label
        compact
        :disabled="disabled || upscale.models === null"
        data-testid="upscale-model"
        @update:model-value="upscale.setModel"
      />
      <span v-if="target" class="upc-target" data-testid="upscale-target"
        >→ {{ target }}</span
      >
    </div>
    <p
      v-if="upscale.models && !upscale.models.length"
      class="upc-note"
      data-testid="upscale-no-model"
    >
      No upscale model on the shelf, so this resizes and adds no detail. Add one
      in Models.
    </p>
  </div>
</template>

<script setup>
import { computed, onMounted } from "vue";
import Segmented from "../widgets/Segmented.vue";
import AppSelect from "../widgets/AppSelect.vue";
import { UPSCALE_FACTORS, useUpscaleStore } from "../../stores/useUpscaleStore";

const props = defineProps({
  /** `{width, height}` of what is upscaled, for "→ W × H"; null when unknown. */
  size: { type: Object, default: null },
  /** The verb the controls are named after, for a screen reader. */
  label: { type: String, default: "Upscale" },
  disabled: { type: Boolean, default: false },
});

const upscale = useUpscaleStore();

const factorOptions = computed(() =>
  UPSCALE_FACTORS.map((factor) => ({
    id: factor,
    label: `${factor}×`,
    disabled: props.disabled,
  })),
);

const modelOptions = computed(() => [
  ...(upscale.models || []).map((m) => ({ label: m.name, value: m.sha256 })),
  { label: "Resize (Lanczos)", value: "" },
]);

const target = computed(() => {
  const { width, height } = props.size || {};
  if (!(width > 0 && height > 0)) return "";
  const by = upscale.factor;
  return `${Math.round(width * by)} × ${Math.round(height * by)}`;
});

onMounted(() => {
  if (upscale.models === null) upscale.loadModels();
});
</script>

<style scoped>
.upc {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.upc-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.upc-model {
  min-width: 0;
  flex: 1 1 160px;
}

.upc-target {
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.upc-note {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
