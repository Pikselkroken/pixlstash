<template>
  <!-- Wraps at spaces, then at camelCase / snake_case joins; a chunk still
       wider than its container ellipsizes, and the tip has it whole. The
       caller's class sets the type. -->
  <p class="breakable-title">
    <Tooltip :text="name" activator="parent" :describe="false" /><template
      v-for="(word, w) in words"
      :key="w"
      >{{ w ? " " : "" }}<template v-for="(chunk, c) in word" :key="c"
        ><wbr v-if="c" /><span class="breakable-title-chunk">{{
          chunk
        }}</span></template
      ></template
    >
  </p>
</template>

<script setup>
import { computed } from "vue";

import { breakableName } from "../../utils/breakableName";
import Tooltip from "./Tooltip.vue";

const props = defineProps({
  name: { type: String, default: "" },
});

const words = computed(() => breakableName(props.name));
</script>

<style scoped>
/* `contain: inline-size` stops the title's widest piece from widening the
   rail through any intrinsically sized ancestor; it takes the width it is
   given and its pieces wrap or ellipsize inside it. */
.breakable-title {
  contain: inline-size;
  margin: 0;
}

/* One camelCase / snake_case piece of the title (`breakableName`). Inline-block
   so a piece wider than the rail ellipsizes on its own line instead of
   scrolling the inspector sideways. */
.breakable-title-chunk {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
</style>
