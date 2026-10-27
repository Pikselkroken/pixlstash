<template>
  <!-- The tip under the pointer while model rows are dragged over a folder
       (#2204): where they go, rename or copy, and what the other drive gets
       back. Its own component so the drag's pointer moves repaint only this,
       never the shelf. Not announced: a drag is a pointer gesture, and the
       Move dialog it opens states the same facts. -->
  <Teleport to="body">
    <div
      v-if="tip"
      class="mdt"
      aria-hidden="true"
      data-testid="model-drop-tip"
      :style="{ left: `${tip.x}px`, top: `${tip.y}px` }"
    >
      {{ tip.text }}
    </div>
  </Teleport>
</template>

<script setup>
import { computed } from "vue";

import { useModelShelfStore } from "../../stores/useModelShelfStore";

const store = useModelShelfStore();
const tip = computed(() => store.dropTip);
</script>

<style scoped>
/* The Tooltip surface (docs/design: tooltips), offset below and right of the
   pointer so it never sits under the drop. */
.mdt {
  position: fixed;
  z-index: var(--z-dropdown);
  transform: translate(var(--space-5), var(--space-5));
  width: max-content;
  max-width: var(--tooltip-max-w);
  padding: var(--space-2) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: var(--elevation-3);
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  pointer-events: none;
}
</style>
