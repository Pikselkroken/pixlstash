<template>
  <!-- `role="alert"`: a refusal is the higher-priority announcement on this
       surface, and it arrives after the form the reader is already looking at. -->
  <div class="rrn" role="alert">
    <v-icon class="rrn-glyph" size="18">mdi-alert-circle-outline</v-icon>
    <div class="rrn-body">
      <p class="rrn-text">
        <b v-if="subject">{{ subject }} can't run.</b>
        {{ read.text }}
      </p>
      <ul v-if="read.files.length" class="rrn-files">
        <li v-for="file in read.files" :key="file.file">
          <span class="rrn-mono">{{ file.file }}</span>
          <template v-if="file.folder">
            , expected in <span class="rrn-mono">models/{{ file.folder }}</span>
          </template>
        </li>
      </ul>
      <div class="rrn-acts">
        <AppButton
          v-if="read.fix === FIX_SETTINGS"
          size="sm"
          @click="emit('settings')"
        >
          Settings › Compute
        </AppButton>
        <AppButton
          v-else-if="read.fix === FIX_RETRY"
          size="sm"
          icon-left="refresh"
          :loading="busy"
          @click="emit('retry')"
        >
          Retry
        </AppButton>
        <AppButton
          v-else-if="read.fix === FIX_DROP_LORA"
          size="sm"
          @click="emit('drop-lora')"
        >
          Run without the LoRA
        </AppButton>
        <a
          v-else
          class="rrn-help"
          :href="HELP_URL"
          target="_blank"
          rel="noopener noreferrer"
        >
          What this means
        </a>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * One reason a run would not happen, with the thing to do about it (F5).
 *
 * The notice surface's error shape: a `--rail-w` rail in `--surface-error`
 * down the left edge, the glyph in the same hue, and the sentence left as
 * ordinary text (docs/design/notice-surface.md).
 */
import { computed } from "vue";
import { VIcon } from "vuetify/components";

import {
  FIX_DROP_LORA,
  FIX_RETRY,
  FIX_SETTINGS,
  readReason,
} from "../../utils/runReasons";
import AppButton from "../widgets/AppButton.vue";

const props = defineProps({
  /** One entry of a pre-flight group's `reasons`: a code plus its payload. */
  reason: { type: Object, required: true },
  /** Which card this is about; omitted when there is only the one. */
  subject: { type: String, default: "" },
  /** A retry is in flight. */
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["settings", "retry", "drop-lora"]);

// A page that exists. There is no per-reason help article to link to, so this
// is the project's own ComfyUI page rather than an invented deep link: seven
// refusal codes fall through to it, and a 404 would be worse than the sentence
// alone.
const HELP_URL = "https://pixlstash.dev/comfyui.html";

const read = computed(() => readReason(props.reason));
</script>

<style scoped>
.rrn {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-left: var(--rail-w) solid rgb(var(--v-theme-surface-error));
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.rrn-glyph {
  color: rgb(var(--v-theme-surface-error));
}

.rrn-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.rrn-text {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.rrn-files {
  margin: 0;
  padding-left: var(--space-5);
  font-size: var(--text-xs);
}

.rrn-mono {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.rrn-acts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.rrn-help {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
}
</style>
