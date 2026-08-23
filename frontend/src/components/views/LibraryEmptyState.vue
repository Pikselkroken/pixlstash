<script setup>
/**
 * The first screen of every install.
 *
 * It used to read *"No pictures in the database. Add pictures by dragging them
 * here."* Two things were wrong with that. **"Database"** is our word, not the
 * owner's — they have pictures, and the thing holding them is an implementation
 * detail they were never introduced to. And **dragging is one route of three**,
 * offered as though it were the only one: PixlStash can also read a folder you
 * already have, in place, without moving anything, and it can generate straight
 * into the library. Someone who arrived with an organised folder tree was being
 * told to take it apart and drag it back in.
 *
 * So: three routes out, folder first, and none of them presented as the
 * official one. Folder leads because it is the case this release exists for and
 * the one that was invisible — reference folders have always worked and were a
 * sidebar accessory nobody was pointed at.
 *
 * **Only for a genuinely empty library.** A filtered-empty grid and an empty
 * scrap heap are different states with different answers, and they keep the
 * card they had; offering "choose a folder" to someone whose filter matched
 * nothing would be answering a question they did not ask.
 */
import { ref } from "vue";
import { VIcon } from "vuetify/components";

import AppButton from "../widgets/AppButton.vue";

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
  if (files.length) emit("add-files", files);
}
</script>

<template>
  <div class="library-empty">
    <div class="library-empty__card">
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
            <span class="library-empty__heading">Use a folder you already have</span>
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
        accept="image/*,video/*"
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

.library-empty__illustration {
  width: 45%;
  max-width: 220px;
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

/* Off-screen rather than `display: none`: a hidden input still has to be
   clickable for `.click()` to open the picker in every browser. */
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
