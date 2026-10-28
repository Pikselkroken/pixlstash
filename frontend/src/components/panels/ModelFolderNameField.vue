<template>
  <!-- The name field for a folder being created or renamed (#2206). One field
       for every place a folder is named: the tree rows, the Folders heading and
       the Move dialog's pick step. It only reports keys; the caller creates or
       renames, and puts the server's refusal back in `error`, so the field stays
       open with what was typed. -->
  <div class="mnf" :class="{ 'mnf--error': error }">
    <input
      :id="inputId"
      ref="inputEl"
      v-model="draft"
      class="mnf-input"
      type="text"
      autocomplete="off"
      spellcheck="false"
      :placeholder="placeholder"
      :aria-label="label"
      :aria-invalid="error ? 'true' : undefined"
      :aria-describedby="hintId"
      data-testid="model-folder-name"
      @keydown="onKeydown"
    />
    <p :id="hintId" class="mnf-hint" :role="error ? 'alert' : undefined">
      {{ error || hint }}
    </p>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, useId, watch } from "vue";

// `useId`, not a counter: inside <script setup> a counter restarts for every
// instance, and two open fields would share one id.
const uid = useId();
const hintId = `mnf-hint-${uid}`;
const inputId = `mnf-input-${uid}`;

const props = defineProps({
  /** The name as typed. */
  modelValue: { type: String, default: "" },
  label: { type: String, required: true },
  hint: {
    type: String,
    default: "Enter creates it here · Esc cancels",
  },
  /** The server's refusal, or "". Replaces the hint, in the danger ink. */
  error: { type: String, default: "" },
  placeholder: { type: String, default: "" },
  /** Select the whole name on open: a rename starts from the old one. */
  select: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue", "submit", "cancel"]);

const inputEl = ref(null);
// Local draft, so typing never waits on the parent's reactivity.
const draft = ref(props.modelValue);

watch(
  () => props.modelValue,
  (value) => {
    if (value !== draft.value) draft.value = value;
  },
);

// Reported from a watcher rather than an input handler, so the parent always
// holds what the field shows, whichever listener the browser ran first.
watch(draft, (value) => emit("update:modelValue", value));

/**
 * Enter applies and Escape cancels. Every key stops here: the field sits inside
 * the tree, whose arrows walk rows, inside a dialog whose Escape closes it, and
 * inside the shelf, whose window listeners would take Delete and Ctrl+A.
 */
function onKeydown(event) {
  event.stopPropagation();
  if (event.key === "Enter") {
    event.preventDefault();
    emit("submit");
  } else if (event.key === "Escape") {
    event.preventDefault();
    emit("cancel");
  }
}

onMounted(async () => {
  await nextTick();
  inputEl.value?.focus();
  if (props.select) inputEl.value?.select();
});
</script>

<style scoped>
.mnf {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

/* 24px, the field's own height; a 1px inset border, so the field reads as a
   field before it has focus. Focused, the global ring covers it. */
.mnf-input {
  display: block;
  width: 100%;
  height: var(--control-h-sm);
  box-sizing: border-box;
  padding: 0 var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-background), 0.06);
  box-shadow: inset 0 0 0 1px rgb(var(--v-theme-border));
  color: rgb(var(--v-theme-on-background));
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  outline: none;
  -webkit-user-select: text;
  user-select: text;
}

.mnf-hint {
  margin: 0;
  font-size: var(--text-2xs);
  color: rgb(var(--v-theme-on-surface-variant));
}

.mnf--error .mnf-hint {
  color: rgb(var(--v-theme-surface-error));
}
</style>
