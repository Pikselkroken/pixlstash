<!--
  One keyboard key, as a keycap (visual-language.md, "The key-hint badge").
  Keys whose usual mark is a symbol that renders as a speck (Shift, ⌘, Enter,
  Backspace) are drawn as icons; every other key is its own text. `v-icon` is
  aria-hidden, so an icon key carries its name as hidden text. Every key ends
  in a hidden space, so a chord reads "Ctrl Z", not "CtrlZ". A chord is
  several KeyCaps side by side; the global `kbd + kbd` rule sets their gap.

  The root is the `<kbd>` itself, so a host's scoped keycap rule still styles it.
-->
<template>
  <kbd :class="{ 'keycap--icon': icon }"
    ><template v-if="icon"
      ><v-icon size="1.1em" :icon="icon" /><span class="visually-hidden">{{
        `${name} `
      }}</span></template
    ><template v-else
      >{{ k
      }}<span class="visually-hidden">{{ " " }}</span></template
    ></kbd
  >
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  // The key as written in a hint: "Shift", "⇧", "⌘", "Enter", "↵", "Tab", "Z".
  k: { type: String, required: true },
});

const ICON_KEYS = {
  Shift: ["mdi-apple-keyboard-shift", "Shift"],
  "⇧": ["mdi-apple-keyboard-shift", "Shift"],
  "⌘": ["mdi-apple-keyboard-command", "Command"],
  Enter: ["mdi-subdirectory-arrow-left", "Enter"],
  "↵": ["mdi-subdirectory-arrow-left", "Enter"],
  "⏎": ["mdi-subdirectory-arrow-left", "Enter"],
  Backspace: ["mdi-keyboard-backspace", "Backspace"],
  "⌫": ["mdi-keyboard-backspace", "Backspace"],
};

const icon = computed(() => ICON_KEYS[props.k]?.[0] ?? null);
const name = computed(() => ICON_KEYS[props.k]?.[1] ?? props.k);
</script>
