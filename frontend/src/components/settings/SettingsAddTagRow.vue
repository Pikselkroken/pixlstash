<template>
  <div class="add-tag-row">
    <div class="add-tag-row__input">
      <AppInput
        :model-value="modelValue"
        :placeholder="placeholder"
        @update:model-value="emit('update:modelValue', $event)"
        @enter="emit('add')"
      />
    </div>
    <AppButton variant="primary_green" icon-left="plus" @click="emit('add')">
      {{ btn }}
    </AppButton>
  </div>
</template>

<script setup>
import AppInput from "../widgets/AppInput.vue";
import AppButton from "../widgets/AppButton.vue";

defineProps({
  modelValue: { type: String, default: "" },
  placeholder: { type: String, default: "" },
  btn: { type: String, default: "Add tag" },
});

const emit = defineEmits(["update:modelValue", "add"]);
</script>

<style scoped>
.add-tag-row {
  display: flex;
  gap: var(--space-4);
  align-items: flex-end;
}

.add-tag-row__input {
  flex: 1;
}
</style>
