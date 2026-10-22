<template>
  <!-- One "Edit stack ▸" row holds every action on an existing stack: the
       order (Make cover / Move earlier / Move later), taking it apart, and
       Keep cover only. Shared by the picture menu, the lightbox menu and the
       selection pill's menu, so the three cannot drift apart. -->
  <div
    v-if="target && (!orderOnly || target.kind === 'member')"
    class="ctx-submenu-wrap"
    @mouseenter="open = true"
    @mouseleave="open = false"
    @focusin="open = true"
    @focusout="onFocusOut"
  >
    <button
      class="ctx-item"
      role="menuitem"
      aria-haspopup="menu"
      :aria-expanded="open ? 'true' : 'false'"
      @click="open = !open"
    >
      <v-icon class="ctx-icon">mdi-layers-edit</v-icon>
      Edit stack
      <v-icon class="ctx-arrow">mdi-chevron-right</v-icon>
    </button>
    <div v-if="open" class="ctx-submenu edit-stack-submenu" role="menu">
      <div class="ctx-label section-label edit-stack-head">
        <span>{{ heading }}</span>
        <span v-if="subheading">{{ subheading }}</span>
      </div>

      <template v-if="target.kind === 'member'">
        <button
          class="ctx-item"
          role="menuitem"
          :disabled="readOnly || target.position === 0"
          :aria-keyshortcuts="keys.cover.aria"
          @click="emit('stack-move', 'cover')"
        >
          <v-icon class="ctx-icon">mdi-star-outline</v-icon>
          <span>Make cover</span>
          <span class="ctx-shortcut" aria-hidden="true">{{ keys.cover.hint }}</span>
        </button>
        <button
          class="ctx-item"
          role="menuitem"
          :disabled="readOnly || target.position === 0"
          :aria-keyshortcuts="keys.earlier.aria"
          @click="emit('stack-move', 'earlier')"
        >
          <v-icon class="ctx-icon">mdi-arrow-left</v-icon>
          <span>Move earlier</span>
          <span class="ctx-shortcut" aria-hidden="true">{{ keys.earlier.hint }}</span>
        </button>
        <button
          class="ctx-item"
          role="menuitem"
          :disabled="readOnly || target.position >= target.count - 1"
          :aria-keyshortcuts="keys.later.aria"
          @click="emit('stack-move', 'later')"
        >
          <v-icon class="ctx-icon">mdi-arrow-right</v-icon>
          <span>Move later</span>
          <span class="ctx-shortcut" aria-hidden="true">{{ keys.later.hint }}</span>
        </button>
      </template>
      <!-- Reordering needs the pictures on screen, so a collapsed stack offers
           to open instead of offering moves nobody can see. -->
      <button
        v-else-if="target.kind === 'collapsed' && !orderOnly"
        class="ctx-item"
        role="menuitem"
        @click="emit('open-stack')"
      >
        <v-icon class="ctx-icon">mdi-arrow-expand-horizontal</v-icon>
        Open stack
      </button>

      <template v-if="!orderOnly">
        <div
          v-if="target.kind === 'member' || target.kind === 'collapsed'"
          class="ctx-sep"
          role="separator"
        />
        <!-- Two rows, because one word used to mean both: "Unstack" dissolved a
             collapsed stack but took only the selection out of an open one. -->
        <button
          v-if="target.kind === 'member' || target.kind === 'members'"
          class="ctx-item edit-stack-two"
          role="menuitem"
          :disabled="readOnly"
          @click="emit('remove-from-stack')"
        >
          <v-icon class="ctx-icon">mdi-layers-remove</v-icon>
          <span class="edit-stack-text"
            >{{ takeOutLabel
            }}<span class="edit-stack-sub">{{ takeOutSub }}</span></span
          >
        </button>
        <button
          class="ctx-item"
          role="menuitem"
          :disabled="readOnly"
          @click="emit('dissolve-stacks')"
        >
          <v-icon class="ctx-icon">mdi-layers-off</v-icon>
          {{ unstackLabel }}
        </button>
        <template v-if="keepCoverOnlyStackCount > 0">
          <div class="ctx-sep" role="separator" />
          <button
            class="ctx-item ctx-item--danger edit-stack-two"
            role="menuitem"
            :disabled="readOnly || !!keepCoverOnlyLockReason"
            @click="emit('keep-cover-only')"
          >
            <Tooltip
              v-if="keepCoverOnlyLockReason"
              :text="keepCoverOnlyLockReason"
              activator="parent"
            />
            <v-icon class="ctx-icon">{{ KEEP_COVER_ONLY_ICON }}</v-icon>
            <span class="edit-stack-text"
              >{{ keepLabel
              }}<span class="edit-stack-sub">{{ keepSub }}</span></span
            >
          </button>
        </template>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import { isApplePlatform } from "../../utils/shortcutHints.js";
import { stackOrdinal } from "../../utils/stack.js";
import {
  KEEP_COVER_ONLY_ICON,
  keepCoverOnlyMenuLabel,
} from "../../utils/keepCoverOnly";
import { plural } from "../../utils/lockedDelete";
import Tooltip from "./Tooltip.vue";

const props = defineProps({
  /**
   * What the selection is, as far as stacks go, or null for no row at all:
   *   {kind: "member", count, position}  one picture of an open stack
   *   {kind: "members", count, selected} several pictures of one open stack
   *   {kind: "collapsed", count}         a collapsed stack's tile
   *   {kind: "stacks", stacks}           more than one stack, or a stack and
   *                                      loose pictures
   * `position` is 0 for the cover.
   */
  target: { type: Object, default: null },
  readOnly: { type: Boolean, default: false },
  // The lightbox offers only the order: taking a stack apart there would
  // rebuild the filmstrip under the picture being looked at. With nothing to
  // order (anything but one picture of a stack) there is no row at all, rather
  // than a submenu holding only its heading.
  orderOnly: { type: Boolean, default: false },
  keepCoverOnlyStackCount: { type: Number, default: 0 },
  keepCoverOnlyLockReason: { type: String, default: null },
  selectedCount: { type: Number, default: 0 },
});

const emit = defineEmits([
  "stack-move",
  "open-stack",
  "remove-from-stack",
  "dissolve-stacks",
  "keep-cover-only",
]);

const open = ref(false);

// Focus leaving the row and its submenu shuts the submenu, as the pointer
// leaving it does; moving between the row and its own items does not.
function onFocusOut(event) {
  if (!event.currentTarget.contains(event.relatedTarget)) open.value = false;
}

const keys = computed(() => {
  const apple = isApplePlatform();
  const mod = apple ? "⌘" : "Ctrl+";
  const aria = apple ? "Meta" : "Control";
  return {
    cover: { hint: `${mod}Home`, aria: `${aria}+Home` },
    earlier: { hint: `${mod}←`, aria: `${aria}+ArrowLeft` },
    later: { hint: `${mod}→`, aria: `${aria}+ArrowRight` },
  };
});

const heading = computed(() => {
  const t = props.target;
  if (t?.kind === "stacks") {
    return `${t.stacks} ${plural(t.stacks, "stack", "stacks")} selected`;
  }
  return `Stack of ${t?.count ?? 0}`;
});

const subheading = computed(() => {
  const t = props.target;
  if (t?.kind === "member") return `this is ${stackOrdinal(t.position + 1)}`;
  if (t?.kind === "members") return `${t.selected} selected`;
  return "";
});

const takeOutLabel = computed(() =>
  props.target?.kind === "members"
    ? `Take ${props.target.selected} out of stack`
    : "Take out of stack",
);

const takeOutSub = computed(() =>
  props.target?.kind === "members"
    ? "They become loose pictures"
    : "Becomes a loose picture",
);

const unstackLabel = computed(() => {
  const t = props.target;
  if (t?.kind === "stacks") {
    return `Unstack ${t.stacks} ${plural(t.stacks, "stack", "stacks")}`;
  }
  return `Unstack all ${t?.count ?? 0}`;
});

const keepLabel = computed(() =>
  props.target?.kind === "stacks"
    ? keepCoverOnlyMenuLabel({
        stackCount: props.keepCoverOnlyStackCount,
        selectedCount: props.selectedCount,
      })
    : keepCoverOnlyMenuLabel(),
);

const keepSub = computed(() => {
  const t = props.target;
  if (t?.kind === "stacks") {
    const n = props.keepCoverOnlyStackCount;
    return n === 1
      ? "Every picture but the cover moves to the Scrapheap."
      : `Every picture but the ${n} covers moves to the Scrapheap.`;
  }
  const others = Math.max(0, (t?.count ?? 0) - 1);
  return others === 1
    ? "The other picture moves to the Scrapheap."
    : `The other ${others} move to the Scrapheap.`;
});
</script>

<style scoped>
/* Wide enough for the second lines to wrap at a readable measure. */
.edit-stack-submenu {
  min-width: 260px;
}

.edit-stack-head {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
}

.edit-stack-two {
  align-items: flex-start;
  padding-top: var(--space-2);
  padding-bottom: var(--space-2);
}

.edit-stack-two .ctx-icon {
  margin-top: var(--space-1);
}

.edit-stack-text {
  display: flex;
  flex-direction: column;
  white-space: normal;
}

.edit-stack-sub {
  font-size: var(--text-xs);
  opacity: var(--opacity-text-secondary);
}
</style>
