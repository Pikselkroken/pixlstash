<template>
  <label class="app-input">
    <FieldLabel v-if="label">{{ label }}</FieldLabel>
    <div class="app-input__wrap">
      <v-icon v-if="icon" size="18" class="app-input__icon"
        >mdi-{{ icon }}</v-icon
      >
      <input
        ref="inputRef"
        class="app-input__field"
        :type="type"
        :value="modelValue"
        :placeholder="placeholder"
        :disabled="disabled"
        @input="emit('update:modelValue', $event.target.value)"
        @blur="emit('blur')"
        @keydown.enter="emit('enter')"
      />
    </div>
  </label>
</template>

<script setup>
import { ref } from "vue";
import { VIcon } from "vuetify/components";
import FieldLabel from "./FieldLabel.vue";

defineProps({
  modelValue: { type: [String, Number], default: "" },
  label: { type: String, default: "" },
  placeholder: { type: String, default: "" },
  icon: { type: String, default: "" },
  type: { type: String, default: "text" },
  disabled: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue", "enter", "blur"]);

const inputRef = ref(null);

defineExpose({
  focus: () => inputRef.value?.focus(),
  select: () => inputRef.value?.select(),
});
</script>

<style scoped>
.app-input {
  display: block;
}

.app-input__wrap {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  padding: 0 var(--space-3);
  height: 27px;
}

/* The field inside is borderless and outline-free, so the wrap draws the
   global ink ring for it. */
.app-input__wrap:has(.app-input__field:focus-visible) {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
}

.app-input__icon {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.app-input__field {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-base);
}

.app-input__field::placeholder {
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.app-input__field:disabled {
  color: rgba(var(--v-theme-on-surface), 0.45);
}
</style>
