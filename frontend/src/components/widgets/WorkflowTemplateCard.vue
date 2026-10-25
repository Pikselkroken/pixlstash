<template>
  <article
    class="wf-card wtc"
    :class="{ 'wf-card--selected': selected }"
    role="group"
    :aria-label="accessibleName"
    data-testid="template-card"
  >
    <!-- One cell, the template's own picture. A template with none, or one
         that will not load, says so on the cover's quiet ground rather than
         drawing a broken image or a blank. -->
    <div class="wf-card__cover wf-card__cover--1">
      <span class="wf-card__pic">
        <img
          v-if="thumbnailUrl && !thumbnailFailed"
          :src="thumbnailUrl"
          alt=""
          loading="lazy"
          @error="thumbnailFailed = true"
        />
        <span v-else class="wtc__no-preview" data-testid="template-no-preview">
          <ComfyuiIcon :size="48" />
          <span class="wf-card__empty-line">Workflow has no preview</span>
        </span>
      </span>
    </div>

    <div class="wf-card__meta wtc__meta">
      <div class="wf-card__row wf-card__row--name">
        <span class="wf-card__name" aria-hidden="true">{{ template.title }}</span>
      </div>
      <!-- What its loaders name: solid where the shelf has the file, hollow
           amber where it does not. Names are in each mark's tooltip. -->
      <div class="wf-card__row">
        <ul v-if="files.length" class="wtc__strip" aria-hidden="true">
          <li
            v-for="file in shownFiles"
            :key="`${file.directory}/${file.name}`"
            class="wtc-mark"
            :class="{ 'wtc-mark--missing': !file.present }"
            data-testid="template-mark"
          >
            <Tooltip
              :text="`${file.name} · ${file.present ? 'on your shelf' : 'not on your shelf'}`"
              activator="parent"
              :describe="false"
            />
            {{ templateFileLetter(file) }}
          </li>
          <li v-if="overflow" class="wf-card__mark-more">+{{ overflow }}</li>
        </ul>
        <span v-else class="wf-card__none" aria-hidden="true">{{
          template.source === "partner" ? partnerText : "Lists no model files"
        }}</span>
      </div>
      <div
        class="wf-card__row wtc__state"
        :class="`wtc__state--${readiness}`"
        aria-hidden="true"
      >
        <v-icon size="14">{{ STATE_ICONS[readiness] }}</v-icon>
        <span data-testid="template-readiness">{{ stateText }}</span>
      </div>
    </div>

    <!-- Where it comes from, top-start as a workflow card's origin badge is:
         nothing on this tab may read as the owner's own work. PixlStash's own
         pack gets its own mark rather than the puzzle piece. The tip says
         what the label stands for; the card's name says the source too. -->
    <div class="wf-card__start">
      <span class="wf-card__badge wtc__source" data-testid="template-source-badge">
        <Tooltip :text="sourceTip" activator="parent" :describe="false" />
        <ComfyuiIcon v-if="template.source === 'builtin'" :size="12" />
        <PixlstashIcon
          v-else-if="ours"
          :size="12"
          data-testid="template-source-pixlstash"
        />
        <v-icon v-else size="12">{{
          template.source === "pack" ? "mdi-puzzle-outline" : "mdi-crown"
        }}</v-icon>
        <span data-testid="template-source">{{ sourceText }}</span>
      </span>
    </div>
  </article>
</template>

<script setup>
// One template on the Workflows view's Templates tab (#2000): the workflow
// card's frame (`cardCover.css` and the `wf-card` rules it shares), so the
// grid reads as one grid on both tabs, with a template's facts in it - its
// picture, where it comes from, the files it would download and whether it
// could run here as things stand.
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { templateThumbnailUrl } from "../../api/comfyui";
import {
  isPixlstashPack,
  missingCount,
  templateFileLetter,
  templateReadiness,
} from "../../stores/useWorkflowTemplatesStore";
import ComfyuiIcon from "./ComfyuiIcon.vue";
import PixlstashIcon from "./PixlstashIcon.vue";
import Tooltip from "./Tooltip.vue";

// Marks drawn before "+N": what fits the strip at the grid's 240px floor.
const STRIP_SLOTS = 6;

const STATE_ICONS = {
  ready: "mdi-check-circle-outline",
  missing: "mdi-download-outline",
  pack_not_loaded: "mdi-puzzle-remove-outline",
  // ComfyUI's own mark for a paid template: its "Partner API" badge is a crown.
  partner: "mdi-crown",
};

const props = defineProps({
  /** One entry of `GET /comfyui/templates`. */
  template: { type: Object, required: true },
  selected: { type: Boolean, default: false },
});

const thumbnailFailed = ref(false);
watch(
  () => props.template.key,
  () => (thumbnailFailed.value = false),
);
const thumbnailUrl = computed(() =>
  props.template.thumbnail ? templateThumbnailUrl(props.template) : "",
);

const files = computed(() => props.template.files ?? []);
const shownFiles = computed(() => files.value.slice(0, STRIP_SLOTS));
const overflow = computed(() => Math.max(0, files.value.length - STRIP_SLOTS));
const readiness = computed(() => templateReadiness(props.template));

const stateText = computed(() => {
  switch (readiness.value) {
    case "ready":
      return "Ready to run";
    case "missing":
      return `${missingCount(props.template)} of ${files.value.length} missing`;
    case "pack_not_loaded":
      return "Pack not loaded";
    default:
      return "Partner API";
  }
});

// ComfyUI's words for a paid template, after who its API nodes call.
const partnerText = computed(() =>
  [...(props.template.providers ?? []), "Runs with credits"].join(" · "),
);

const ours = computed(() => isPixlstashPack(props.template));

const sourceText = computed(() => {
  if (props.template.source === "builtin") return "ComfyUI";
  if (ours.value) return "PixlStash";
  if (props.template.source === "pack") return props.template.module;
  return "Partner API";
});

const sourceTip = computed(() => {
  if (props.template.source === "builtin") return "Comes with ComfyUI";
  if (ours.value) {
    return "From the ComfyUI-PixlStash nodes, PixlStash's own pack for ComfyUI";
  }
  if (props.template.source === "pack") {
    return `From the ${props.template.module} node pack`;
  }
  return "Runs on Comfy's paid API";
});

const accessibleName = computed(() =>
  [
    props.template.title,
    `${sourceText.value} template`,
    stateText.value,
  ].join(", "),
);
</script>

<style scoped src="./cardCover.css"></style>
<style scoped>
/* The workflow card's frame (`WorkflowCard.vue`), so both tabs draw one grid:
   the same border, radius, selection overlay, top-anchored cover crop and
   two-line name. Only what a template card shows is carried over. */
.wf-card {
  --wf-name-h: 36px;

  position: relative;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow: hidden;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
}

.wf-card--selected::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: var(--z-raised);
  border-radius: inherit;
  background: var(--active-wash);
  box-shadow: var(--selection-ring);
  pointer-events: none;
}

.wf-card__pic {
  position: relative;
  overflow: hidden;
  background: rgb(var(--v-theme-input-background));
}

.wf-card__pic img {
  display: block;
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: top center;
}

.wtc__no-preview {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wf-card__start {
  position: absolute;
  top: var(--space-3);
  left: var(--space-3);
  z-index: var(--z-raised);
  display: flex;
  gap: var(--space-2);
  pointer-events: none;
}

.wf-card__start > .wf-card__badge {
  position: static;
}

/* Hoverable for its tip. A click on it still bubbles to the grid row that
   selects the card. */
.wtc__source {
  pointer-events: auto;
}

.wf-card__mark-more {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The name's two lines, the strip and the state line. */
.wtc__meta {
  flex: none;
  display: grid;
  grid-template-rows: var(--wf-name-h) var(--control-h-sm) var(--control-h-sm);
  row-gap: var(--space-1);
  padding: var(--space-3);
}

.wf-card__row--name {
  align-items: flex-start;
  white-space: normal;
}

.wf-card__name {
  flex: 1;
  min-width: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  line-height: calc(var(--wf-name-h) / 2);
}

.wtc__strip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  margin: 0;
  padding: 0;
  overflow: hidden;
  list-style: none;
}

/* A file's mark: the model's or the kind's initial on a quiet tile, or a
   hollow amber outline where the shelf lacks it. */
.wtc-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  box-sizing: border-box;
  width: var(--entity-thumb);
  height: var(--entity-thumb);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, rgb(var(--v-theme-on-surface)) 12%, transparent);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
}

.wtc-mark--missing {
  border-color: rgb(var(--v-theme-warning));
  background: transparent;
}

.wtc__state {
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wtc__state--ready .v-icon {
  color: rgb(var(--v-theme-success));
}

.wtc__state--missing .v-icon {
  color: rgb(var(--v-theme-warning));
}
</style>
