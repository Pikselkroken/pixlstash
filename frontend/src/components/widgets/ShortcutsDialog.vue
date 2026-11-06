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
import { canCopyFiles } from "../../utils/copyAsFiles";
import AppDialog from "./AppDialog.vue";
import KeyCap from "./KeyCap.vue";

const userPrefs = useUserPrefsStore();

// The undo/redo chords differ per platform, so the table renders whatever the
// hint helper reports rather than hard-coding Ctrl. Select-all differs the same
// way - a macOS reader was being taught `Ctrl+A` for a chord their keyboard
// spells `⌘A`, and the shelf's own keycap now reports the platform.
const undoKeyHintKeys = undoKeyHint();
// The stack reorder keys, spelled as the Edit stack menu spells them, and the
// shelf's cut and paste (#2204), which take the same modifier.
const stackModKey = isApplePlatform() ? "⌘" : "Ctrl";
const redoKeyHintKeys = redoKeyHint();
const selectAllKeyHintKeys = selectAllKeyHint();
const copyFiles = canCopyFiles();

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
          <td><kbd>1</kbd> – <kbd class="kbd-or">5</kbd></td>
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
        <tr v-if="copyFiles">
          <td><KeyCap :k="stackModKey" /><kbd>C</kbd></td>
          <td>Copy selected images as files, to paste into a folder, mail or chat</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><KeyCap k="Ctrl" /><kbd>V</kbd></td>
          <td>Paste copied tags on the selected images</td>
        </tr>
        <tr>
          <td>
            <template v-for="key in selectAllKeyHintKeys" :key="key"
              ><KeyCap :k="key" /></template
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
            <template v-for="key in undoKeyHintKeys" :key="key"
              ><KeyCap :k="key" /></template
            >
          </td>
          <td>Undo the last change</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td>
            <template v-for="key in redoKeyHintKeys" :key="key"
              ><KeyCap :k="key" /></template
            >
          </td>
          <td>Redo the change you just undid</td>
        </tr>
        <tr>
          <td><kbd>G</kbd></td>
          <td>Focus first visible image (start keyboard navigation)</td>
        </tr>
        <tr>
          <td>
            <kbd>←</kbd> <kbd class="kbd-or">→</kbd>
            {{ " " }}<kbd class="kbd-or">↑</kbd> <kbd class="kbd-or">↓</kbd>
          </td>
          <td>Move cursor and select image</td>
        </tr>
        <tr>
          <td><KeyCap k="Shift" /><kbd>Arrow</kbd></td>
          <td>Extend selection</td>
        </tr>
        <tr>
          <td><KeyCap k="Ctrl" /><kbd>Arrow</kbd></td>
          <td>Move cursor without changing selection</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td>
            <KeyCap :k="stackModKey" /><kbd>←</kbd>
            {{ " " }}<KeyCap :k="stackModKey" class="kbd-or" /><kbd>→</kbd>
          </td>
          <td>In an open stack: move the picture earlier or later</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><KeyCap :k="stackModKey" /><kbd>Home</kbd></td>
          <td>In an open stack: make the picture the cover</td>
        </tr>
        <tr>
          <td><kbd>Space</kbd></td>
          <td>Toggle selection of cursor image</td>
        </tr>
        <tr>
          <td><KeyCap k="Enter" /></td>
          <td>Open cursor image</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>Delete</kbd></td>
          <td>Delete selected images</td>
        </tr>
        <tr>
          <td><kbd>Esc</kbd></td>
          <td>Clear the selection, then the search, then the filters</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><KeyCap :k="stackModKey" /><kbd>X</kbd></td>
          <td>On Models: cut the selected models, to move them</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><KeyCap :k="stackModKey" /><kbd>V</kbd></td>
          <td>On Models: move the cut models into the open folder, or into the focused Folders row</td>
        </tr>
        <tr>
          <td><kbd>Esc</kbd></td>
          <td>On Models: put back what was cut</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>F2</kbd></td>
          <td>On Models, in the Folders tree: rename the folder</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><KeyCap :k="stackModKey" /><KeyCap k="Shift" /><kbd>N</kbd></td>
          <td>On Models, in the Folders tree: new folder inside it</td>
        </tr>
        <tr>
          <td><kbd>S</kbd></td>
          <td>Add selected images to a set</td>
        </tr>
        <tr>
          <td><kbd>Home</kbd> / <kbd class="kbd-or">End</kbd></td>
          <td>Jump to first / last image</td>
        </tr>
        <tr>
          <td><kbd>Page Up</kbd> / <kbd class="kbd-or">Page Down</kbd></td>
          <td>Scroll image grid</td>
        </tr>
        <tr>
          <td colspan="2" class="shortcuts-section">Lightbox</td>
        </tr>
        <tr>
          <td><kbd>←</kbd> <kbd class="kbd-or">→</kbd></td>
          <td>Previous / next image</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>1</kbd> – <kbd class="kbd-or">5</kbd></td>
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
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>E</kbd></td>
          <td>Open the Edit tab (on it, with ComfyUI: Eraser)</td>
        </tr>
        <tr>
          <td><KeyCap k="Ctrl" /><kbd>C</kbd></td>
          <td>Copy the selected tags</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><KeyCap k="Ctrl" /><kbd>V</kbd></td>
          <td>Paste copied tags on the open picture</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>[</kbd> / <kbd class="kbd-or">]</kbd></td>
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
          <td><KeyCap :k="stackModKey" /> + Wheel</td>
          <td>Zoom</td>
        </tr>
        <tr>
          <td><KeyCap k="Shift" /> + Wheel</td>
          <td>Previous / next image</td>
        </tr>
        <tr>
          <td><kbd>Esc</kbd></td>
          <td>Close lightbox</td>
        </tr>
        <tr>
          <td>Middle click</td>
          <td>Close lightbox</td>
        </tr>
        <tr>
          <td>Click outside the picture</td>
          <td>Close lightbox</td>
        </tr>
        <tr>
          <td colspan="2" class="shortcuts-section">General</td>
        </tr>
        <tr :class="{ 'shortcut-disabled': isReadOnly }">
          <td><kbd>F2</kbd></td>
          <td>Edit selected character or picture set</td>
        </tr>
        <tr>
          <td><kbd>?</kbd> / <kbd class="kbd-or">F1</kbd></td>
          <td>Show / hide this dialog</td>
        </tr>
      </tbody>
    </table>
  </AppDialog>
</template>
