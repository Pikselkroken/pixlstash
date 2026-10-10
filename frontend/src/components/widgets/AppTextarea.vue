<template>
  <label class="app-textarea">
    <FieldLabel v-if="label">{{ label }}</FieldLabel>
    <span class="app-textarea__box" :class="{ 'app-textarea__box--marked': marked }">
      <!-- A textarea cannot style its own text, so the marks are drawn on a
           copy of it underneath: same box, same font, same wrapping. -->
      <span
        v-if="marked"
        ref="backdrop"
        class="app-textarea__field app-textarea__backdrop"
        aria-hidden="true"
        ><template v-for="(part, index) in parts" :key="index"
          ><mark v-if="part.hit" class="app-textarea__mark">{{ part.text }}</mark
          ><template v-else>{{ part.text }}</template></template
        >{{ "\n" }}</span
      >
      <textarea
        ref="field"
        class="app-textarea__field"
        :value="modelValue"
        :placeholder="placeholder"
        :rows="rows"
        :disabled="disabled"
        @input="emit('update:modelValue', $event.target.value)"
        @scroll="syncScroll"
      />
    </span>
  </label>
</template>

<script setup>
import { computed, ref } from "vue";
import { markWords } from "../../utils/triggerWords";
import FieldLabel from "./FieldLabel.vue";

const props = defineProps({
  modelValue: { type: String, default: "" },
  label: { type: String, default: "" },
  placeholder: { type: String, default: "" },
  rows: { type: [Number, String], default: 3 },
  disabled: { type: Boolean, default: false },
  /** Words to mark where the text names them (whole words, any case). */
  highlight: { type: Array, default: () => [] },
});

const emit = defineEmits(["update:modelValue"]);

const field = ref(null);
const backdrop = ref(null);

const parts = computed(() => markWords(props.modelValue, props.highlight));
const marked = computed(() => parts.value.some((part) => part.hit));

function syncScroll() {
  if (backdrop.value) backdrop.value.scrollTop = field.value.scrollTop;
}

defineExpose({ focus: () => field.value?.focus() });
</script>

<style scoped>
.app-textarea {
  display: block;
}

.app-textarea__box {
  display: block;
}

.app-textarea__field {
  width: 100%;
  resize: vertical;
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-base);
  line-height: var(--leading-snug);
  padding: var(--space-2) var(--space-3);
}

.app-textarea__field::placeholder {
  color: rgba(var(--v-theme-on-surface), 0.45);
}

/* Marked: the copy takes the field's ground and the field goes clear over it.
   A grid so the two share one box exactly, and a gutter held on both so a
   scrollbar arriving does not rewrap one and not the other. */
.app-textarea__box--marked {
  position: relative;
  display: grid;
}

.app-textarea__box--marked .app-textarea__field {
  scrollbar-gutter: stable;
}

.app-textarea__box--marked textarea {
  position: relative;
  background: transparent;
}

.app-textarea__backdrop {
  position: absolute;
  inset: 0;
  overflow: hidden;
  resize: none;
  white-space: pre-wrap;
  overflow-wrap: break-word;
  border-color: transparent;
  color: transparent;
  pointer-events: none;
}

/* Olive, as every chosen thing is: the word belongs to the picked person. */
.app-textarea__mark {
  background: var(--active-wash);
  border-radius: var(--radius-sm);
  color: transparent;
}
</style>
