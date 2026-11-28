<script setup>
/**
 * The footer band every workspace sidebar ends in (#2159): the one amber key
 * action at its label's width, then the workspace's other create verbs as
 * icon bar buttons, each wearing the amber "+". When the key's whole label and
 * the verbs cannot share the band, the verbs fold into a caret on the key (a
 * split button); on the narrow sidebar the band is the key alone, full width.
 *
 * The key is the default slot (an `AppButton`, variant primary). A verb is
 * `{ id, label, icon, mark, run }`: `label` is its tooltip and accessible name,
 * `mark` the `AppBarButton` `createMark` (left out on a verb that sets rather
 * than makes), and `run(el)` is called with the control it was picked from,
 * for a dialog to hand focus back to.
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import { useSidebarWidth } from "../../composables/useSidebarWidth";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { withRef } from "../../utils/withRef.js";

const props = defineProps({
  verbs: { type: Array, default: () => [] },
});

const { narrow } = useSidebarWidth();
const bandEl = ref(null);
const keyEl = ref(null);
const folded = ref(false);
const menuOpen = ref(false);
// The caret outlives its menu, so a dialog a folded verb opens returns there.
let caretEl = null;

/** Fold when the key's unclipped label and the verbs do not fit side by side. */
function measure() {
  const band = bandEl.value;
  const key = keyEl.value?.firstElementChild;
  if (!band || !key) return;
  const style = getComputedStyle(band);
  const room =
    band.clientWidth -
    parseFloat(style.paddingLeft || 0) -
    parseFloat(style.paddingRight || 0);
  // Not laid out (hidden behind another workspace, or no layout engine).
  if (!(room > 0)) return;
  const label = key.querySelector(".app-btn__label");
  const keyWidth =
    key.offsetWidth + (label ? label.scrollWidth - label.clientWidth : 0);
  const gap = parseFloat(style.columnGap) || 0;
  const bar =
    parseFloat(
      getComputedStyle(band).getPropertyValue("--control-h-bar"),
    ) || 32;
  folded.value = keyWidth + props.verbs.length * (bar + gap) > room;
}

let resizeObserver = null;
let mutationObserver = null;

onMounted(() => {
  measure();
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(() => measure());
    resizeObserver.observe(bandEl.value);
    // The key's own size moves when its font loads.
    resizeObserver.observe(keyEl.value);
  }
  // "Import" becomes "Import to Portraits" without the band changing size.
  if (typeof MutationObserver !== "undefined") {
    mutationObserver = new MutationObserver(() => measure());
    mutationObserver.observe(keyEl.value, {
      subtree: true,
      childList: true,
      characterData: true,
    });
  }
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  mutationObserver?.disconnect();
});

watch(
  () => props.verbs.length,
  () => nextTick(measure),
);

function runFolded(verb) {
  menuOpen.value = false;
  verb.run(caretEl?.el ?? caretEl);
}
</script>

<template>
  <div
    ref="bandEl"
    class="sidebar-footer-band sfb"
    :class="{
      'sfb--whole': narrow || !verbs.length,
      'sfb--folded': folded && !narrow && verbs.length,
    }"
  >
    <span ref="keyEl" class="sfb-key">
      <slot />
      <v-menu
        v-if="folded && !narrow && verbs.length"
        v-model="menuOpen"
        location="top end"
        :offset="4"
      >
        <template #activator="{ props: menuProps }">
          <AppButton
            v-bind="withRef(menuProps, (el) => (caretEl = el))"
            class="sfb-caret"
            variant="primary"
            icon-only
            icon-left="menu-down"
            tooltip="More ways to add"
            aria-haspopup="menu"
          />
        </template>
        <div
          class="ctx-menu"
          role="menu"
          aria-label="More ways to add"
          tabindex="-1"
          @keydown="onMenuKeydown"
        >
          <button
            v-for="verb in verbs"
            :key="verb.id"
            type="button"
            class="ctx-item"
            role="menuitem"
            :data-verb="verb.id"
            @click="runFolded(verb)"
          >
            <v-icon class="ctx-icon">{{ verb.icon }}</v-icon>
            <span class="ctx-label-text">{{ verb.label }}</span>
          </button>
        </div>
      </v-menu>
    </span>
    <span v-if="!narrow && !folded && verbs.length" class="sfb-verbs">
      <AppBarButton
        v-for="verb in verbs"
        :key="verb.id"
        :icon="verb.icon"
        :tooltip="verb.label"
        tooltip-location="top"
        :create-mark="verb.mark ?? false"
        :data-verb="verb.id"
        @click="verb.run($event.currentTarget)"
      />
    </span>
  </div>
</template>

<style scoped>
.sfb {
  gap: var(--space-2);
}

/* The key takes its label's width; a label too long for the band clips. */
.sfb-key {
  display: flex;
  min-width: 0;
  flex: 0 1 auto;
}

.sfb-key :deep(.app-btn) {
  min-width: 0;
  height: var(--control-h-bar);
}

.sfb-key :deep(.app-btn__label) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Narrow, or nothing beside it: today's full-width key. */
.sfb--whole .sfb-key {
  flex: 1;
}

.sfb--whole .sfb-key :deep(.app-btn) {
  width: 100%;
}

/* Folded: the key and its caret are one split button, a hairline apart. */
.sfb--folded .sfb-key {
  gap: 1px;
}

.sfb--folded .sfb-key :deep(.app-btn:not(.sfb-caret)) {
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
}

.sfb-key .sfb-caret {
  flex-shrink: 0;
  height: var(--control-h-bar);
  border-top-left-radius: 0;
  border-bottom-left-radius: 0;
}

.sfb-verbs {
  display: flex;
  gap: var(--space-2);
  margin-left: auto;
  flex-shrink: 0;
}
</style>
