<script setup>
/**
 * The first screen of every install - see `docs/frontend_architecture.md`
 * §"The empty library is a different question from an empty grid" for why it
 * says what it says.
 *
 * In short: three routes out rather than one, folder first and the only
 * accented one, and not a word about a "database". Presentational - every
 * route is emitted for the grid to place, including the chosen files, which
 * are handed up unfiltered because the grid already drops what PixlStash
 * cannot read for both drop paths.
 */
import { ref } from "vue";
import { VIcon } from "vuetify/components";

import AppButton from "../widgets/AppButton.vue";
import { IMPORT_FILE_ACCEPT } from "../../utils/media";

const emit = defineEmits(["choose-folder", "add-files", "connect-comfyui"]);

const fileInput = ref(null);

function pickFiles() {
  fileInput.value?.click();
}

function filesChosen(event) {
  const files = Array.from(event.target.files ?? []);
  // Cleared before the emit, so choosing the same files twice in a row still
  // fires `change` the second time.
  event.target.value = "";
  // Handed up unfiltered. `accept` is advisory - every OS picker offers "All
  // Files" - so somebody has to drop what PixlStash cannot read, and that
  // somebody is the grid, where both drop paths already do it against the same
  // `isSupportedImportFile` and raise the same notice. A presenter owning a
  // third copy is how the three drift apart.
  if (files.length) emit("add-files", files);
}
</script>

<template>
  <div class="library-empty">
    <!-- Announced, unlike the two empty cards beside it. This one appears
         asynchronously, at least 350ms after load, and replaces the whole grid
         on the first screen a new install shows - the one place a screen reader
         user most needs to be told the view changed under them. -->
    <div class="library-empty__card" role="status" aria-live="polite">
      <div class="library-empty__illustration" aria-hidden="true">
        <img src="/Empty.png" alt="" />
      </div>

      <h2 class="library-empty__title">This library is empty</h2>
      <p class="library-empty__lead">
        Three ways to put something in it, and none of them is more official
        than the others.
      </p>

      <ul class="library-empty__options">
        <!-- First, and the only one carrying the accent. It is the case this
             release exists for, and the sentence under it is the promise the
             whole release rests on. -->
        <li class="library-empty__option">
          <span class="library-empty__mark" aria-hidden="true">
            <v-icon size="19">mdi-folder-outline</v-icon>
          </span>
          <span class="library-empty__text">
            <span class="library-empty__heading"
              >Use a folder you already have</span
            >
            <span class="library-empty__detail">
              Point PixlStash at one and it reads it where it sits. Nothing is
              moved.
            </span>
          </span>
          <AppButton size="sm" variant="primary" @click="emit('choose-folder')">
            Choose a folder…
          </AppButton>
        </li>

        <li class="library-empty__option">
          <span class="library-empty__mark" aria-hidden="true">
            <v-icon size="19">mdi-tray-arrow-up</v-icon>
          </span>
          <span class="library-empty__text">
            <span class="library-empty__heading">Drop pictures in</span>
            <span class="library-empty__detail">
              Drag them anywhere on this window, or choose them here.
            </span>
          </span>
          <AppButton size="sm" variant="secondary" @click="pickFiles">
            Add files…
          </AppButton>
        </li>

        <li class="library-empty__option">
          <span class="library-empty__mark" aria-hidden="true">
            <v-icon size="19">mdi-graph-outline</v-icon>
          </span>
          <span class="library-empty__text">
            <span class="library-empty__heading">Connect ComfyUI</span>
            <span class="library-empty__detail">
              Generate straight into this library, with the settings and
              workflow kept on every picture.
            </span>
          </span>
          <AppButton
            size="sm"
            variant="secondary"
            @click="emit('connect-comfyui')"
          >
            Connect…
          </AppButton>
        </li>
      </ul>

      <input
        ref="fileInput"
        class="library-empty__file-input"
        type="file"
        multiple
        :accept="IMPORT_FILE_ACCEPT"
        tabindex="-1"
        aria-hidden="true"
        @change="filesChosen"
      />
    </div>
  </div>
</template>

<style scoped>
.library-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  pointer-events: auto;
  z-index: var(--z-raised);
}

.library-empty__card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
  /* Wider than the sibling card's `--notice-max-w` (420px), deliberately: that
     one holds a sentence, this one holds three option rows with a button each,
     and at 420px every one of them wraps. */
  width: min(640px, calc(100% - var(--space-7)));
  box-sizing: border-box;
  margin: auto;
  padding: var(--space-6) var(--space-7);
  border-radius: var(--radius-lg);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-background));
  text-align: center;
  box-shadow: var(--elevation-3);
}

/* Matches `.empty-state-illustration` in ImageGrid.css. The two cards sit in
   the same slot in the same view and show the same file, so a filter toggle
   used to resize the artwork; the cap is what keeps it sane in a card that is
   wider than that one. */
.library-empty__illustration {
  width: 90%;
  max-width: 260px;
  color: rgba(var(--v-theme-on-panel), 0.45);
}

.library-empty__illustration img {
  display: block;
  width: 100%;
  height: auto;
}

.library-empty__title {
  margin: 0;
  font-family: var(--font-pixel);
  font-size: var(--text-2xl);
  font-weight: var(--weight-regular);
  line-height: var(--leading-tight);
}

.library-empty__lead {
  margin: 0;
  max-width: 46ch;
  color: rgba(var(--v-theme-on-background), 0.72);
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.library-empty__options {
  list-style: none;
  margin: var(--space-2) 0 0;
  padding: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  text-align: left;
}

.library-empty__option {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  padding: var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.library-empty__mark {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  border-radius: var(--radius-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(var(--v-theme-accent), 0.16);
  color: rgb(var(--v-theme-accent));
}

.library-empty__text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.library-empty__heading {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.library-empty__detail {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.72);
  line-height: var(--leading-snug);
}

/* Off-screen rather than `display: none`: some browsers refuse to open the
   picker for an input that is not rendered at all. `pointer-events: none` is
   safe on top of that - a programmatic `.click()` ignores it, and nobody should
   be able to hit a 1px target by accident. The button above is the real
   control, and it is a real <button>. */
.library-empty__file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

@media (max-width: 799px) {
  .library-empty__option {
    align-items: flex-start;
    flex-direction: column;
    gap: var(--space-3);
  }
}
</style>
