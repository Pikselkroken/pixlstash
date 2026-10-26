<template>
  <!-- "1 pinned · unpin" beside a choice: values typed over what it set.
       The choice keeps its name; this is how the owner lets them follow it
       again. -->
  <button
    v-if="keys.length"
    type="button"
    class="tld-unpin"
    :aria-label="`${keys.length} value${keys.length === 1 ? '' : 's'} typed by you; unpin to follow this choice again`"
    @click="emit('unpin', keys)"
  >
    <v-icon size="12">mdi-pin-outline</v-icon>
    <span>{{ keys.length }} pinned · unpin</span>
  </button>
</template>

<script setup>
import { VIcon } from "vuetify/components";

defineProps({
  // The pinned setting keys this choice would otherwise set.
  keys: { type: Array, default: () => [] },
});

const emit = defineEmits(["unpin"]);
</script>

<style scoped>
.tld-unpin {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-pill);
  border: 1px solid var(--active-bar);
  background: var(--active-wash);
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-medium);
  cursor: pointer;
}

.tld-unpin:hover {
  background-image: var(--hover-shade);
}
</style>
