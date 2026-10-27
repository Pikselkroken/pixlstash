<script setup>
import { computed } from "vue";
import { isReadOnly } from "../../utils/apiClient";
import {
  isApplePlatform,
  redoKeyHint,
  selectAllKeyHint,
  undoKeyHint,
} from "../../utils/shortcutHints";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import AppDialog from "./AppDialog.vue";

const userPrefs = useUserPrefsStore();

// The undo/redo chords differ per platform, so the table renders whatever the
// hint helper reports rather than hard-coding Ctrl. Select-all differs the same
// way - a macOS reader was being taught `Ctrl+A` for a chord their keyboard
// spells `⌘A`, and the shelf's own keycap now reports the platform.
const undoKeyHintKeys = undoKeyHint();
// The stack reorder keys, spelled as the Edit stack menu spells them.
const stackModKey = isApplePlatform() ? "⌘" : "Ctrl";
const redoKeyHintKeys = redoKeyHint();
const selectAllKeyHintKeys = selectAllKeyHint();

const props = defineProps({
  modelValue: { type: Boolean, default: false },
});
const emit = defineEmits(["update:modelValue"]);

const open = computed(() => props.modelValue);
</script>

<template>
  <AppDialog
    :open="open"
    title="Keyboard shortcuts"
    @close="emit('update:modelValue', false)"
  >
    <table class="shortcuts-table">
      <tbody>
        <tr>
          <td colspan="2" class="shortcuts-section">Grid view</td>
        </tr>
        <tr>
          <td><kbd>F</kbd></td>
          <td>Open search (on Models and Workflows: filter by name)</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>1</kbd> – <kbd>5</kbd></td>
          <td>Set star rating on hovered / selected image(s)</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>H</kbd></td>
          <td>Favourite or unfavourite hovered / selected image(s)</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>T</kbd></td>
          <td>Tag selected images</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>Ctrl</kbd>+<kbd>V</kbd></td>
          <td>Paste copied tags on the selected images</td>
        </tr>
        <tr>
          <td>
            <template v-for="(key, i) in selectAllKeyHintKeys" :key="key"
              ><span v-if="i > 0">+</span><kbd>{{ key }}</kbd></template
            >
          </td>
          <td>Select all images</td>
        </tr>
        <tr>
          <td>
            {{
              userPrefs.gridOpenGesture === "single" ? "Click" : "Double-click"
            }}
          </td>
          <td>Open image (change in Settings › Appearance › Mouse)</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td>
            <template v-for="(key, i) in undoKeyHintKeys" :key="key"
              ><span v-if="i > 0">+</span><kbd>{{ key }}</kbd></template
            >
          </td>
          <td>Undo the last change</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td>
            <template v-for="(key, i) in redoKeyHintKeys" :key="key"
              ><span v-if="i > 0">+</span><kbd>{{ key }}</kbd></template
            >
          </td>
          <td>Redo the change you just undid</td>
        </tr>
        <tr>
          <td><kbd>G</kbd></td>
          <td>Focus first visible image (start keyboard navigation)</td>
        </tr>
        <tr>
          <td><kbd>←</kbd> <kbd>→</kbd> <kbd>↑</kbd> <kbd>↓</kbd></td>
          <td>Move cursor and select image</td>
        </tr>
        <tr>
          <td><kbd>Shift</kbd>+<kbd>Arrow</kbd></td>
          <td>Extend selection</td>
        </tr>
        <tr>
          <td><kbd>Ctrl</kbd>+<kbd>Arrow</kbd></td>
          <td>Move cursor without changing selection</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>{{ stackModKey }}</kbd>+<kbd>←</kbd> <kbd>→</kbd></td>
          <td>In an open stack: move the picture earlier or later</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>{{ stackModKey }}</kbd>+<kbd>Home</kbd></td>
          <td>In an open stack: make the picture the cover</td>
        </tr>
        <tr>
          <td><kbd>Space</kbd></td>
          <td>Toggle selection of cursor image</td>
        </tr>
        <tr>
          <td><kbd>Enter</kbd></td>
          <td>Open cursor image</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>Delete</kbd></td>
          <td>Delete selected images</td>
        </tr>
        <tr>
          <td><kbd>Esc</kbd></td>
          <td>Clear selection</td>
        </tr>
        <tr>
          <td><kbd>S</kbd></td>
          <td>Open selection menu</td>
        </tr>
        <tr>
          <td><kbd>Home</kbd> / <kbd>End</kbd></td>
          <td>Jump to first / last image</td>
        </tr>
        <tr>
          <td><kbd>Page Up</kbd> / <kbd>Page Down</kbd></td>
          <td>Scroll image grid</td>
        </tr>
        <tr>
          <td colspan="2" class="shortcuts-section">Image overlay</td>
        </tr>
        <tr>
          <td><kbd>←</kbd> <kbd>→</kbd></td>
          <td>Previous / next image</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>1</kbd> – <kbd>5</kbd></td>
          <td>Set star rating</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>H</kbd></td>
          <td>Favourite or unfavourite</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>T</kbd></td>
          <td>Add tag</td>
        </tr>
        <tr>
          <td><kbd>Ctrl</kbd>+<kbd>C</kbd></td>
          <td>Copy the selected tags</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>Ctrl</kbd>+<kbd>V</kbd></td>
          <td>Paste copied tags on the open picture</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>[</kbd> / <kbd>]</kbd></td>
          <td>Rotate 90° left / right (JPEG and PNG only)</td>
        </tr>
        <tr>
          <td><kbd>Z</kbd></td>
          <td>Toggle zoom</td>
        </tr>
        <tr>
          <td><kbd>I</kbd></td>
          <td>Toggle info panel</td>
        </tr>
        <tr>
          <td><kbd>{{ stackModKey }}</kbd>+Wheel</td>
          <td>Zoom</td>
        </tr>
        <tr>
          <td><kbd>Shift</kbd>+Wheel</td>
          <td>Previous / next image</td>
        </tr>
        <tr>
          <td><kbd>Esc</kbd></td>
          <td>Close overlay</td>
        </tr>
        <tr>
          <td>Middle click</td>
          <td>Close overlay</td>
        </tr>
        <tr>
          <td>Click outside the picture</td>
          <td>Close overlay</td>
        </tr>
        <tr>
          <td colspan="2" class="shortcuts-section">General</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>F2</kbd></td>
          <td>Edit selected character or picture set</td>
        </tr>
        <tr>
          <td><kbd>?</kbd> / <kbd>F1</kbd></td>
          <td>Show / hide this dialog</td>
        </tr>
      </tbody>
    </table>
  </AppDialog>
</template>
