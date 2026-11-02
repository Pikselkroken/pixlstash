<script setup>
// The upstream program a settings pane connects to (#2521): who makes it and
// where to get it. Two forms, one per state of the pane:
//
// - the strip, inside the setup card while nothing is found: the mark, what the
//   program is, and an outline "Get …" link. The card's own amber action stays
//   the key one, since pointing PixlStash at a program you have is likelier.
// - `compact`, in the section header's action slot once connected: the mark
//   and the site, with maker, licence and description in its tooltip.
//
// Links, not buttons, so the desktop shell hands them to the browser (https
// only, `openExternalSafely`).
import { computed } from "vue";
import { VIcon } from "vuetify/components";
import AiToolkitIcon from "../widgets/AiToolkitIcon.vue";
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import Tooltip from "../widgets/Tooltip.vue";

const PROGRAMS = {
  comfyui: {
    name: "ComfyUI",
    maker: "Comfy Org",
    licence: "GPL-3.0",
    about:
      "The open-source, node-based engine that runs your workflows: models, samplers and upscalers wired as a graph.",
    // Its newest release, whichever it is.
    site: "https://github.com/Comfy-Org/ComfyUI/releases/latest",
    siteLabel: "Latest release",
    get: "https://github.com/Comfy-Org/ComfyUI/releases/latest",
    icon: ComfyuiIcon,
  },
  // Ostris publishes no releases, tags, site or download page: the
  // repository, and its README's Installation section to get it. Move to
  // /releases/latest, as ComfyUI has, if they start publishing releases.
  "ai-toolkit": {
    name: "AI-toolkit",
    maker: "Ostris",
    licence: "MIT",
    about:
      "An all-in-one training suite for diffusion models, built to train LoRAs on consumer GPUs.",
    site: "https://github.com/ostris/ai-toolkit",
    siteLabel: "ostris/ai-toolkit",
    get: "https://github.com/ostris/ai-toolkit#installation",
    icon: AiToolkitIcon,
  },
};

const props = defineProps({
  // A key of PROGRAMS: "comfyui" | "ai-toolkit".
  program: { type: String, required: true },
  compact: { type: Boolean, default: false },
});

const p = computed(() => PROGRAMS[props.program]);
const credit = computed(
  () =>
    `${p.value.name} is a separate, free program by ${p.value.maker} (${p.value.licence}).`,
);
</script>

<template>
  <a
    v-if="compact"
    class="up-link"
    :href="p.site"
    target="_blank"
    rel="noopener noreferrer"
    :aria-label="`${p.name}: ${p.siteLabel}`"
    :data-testid="`upstream-site-${program}`"
  >
    <Tooltip :text="`${credit} ${p.about}`" activator="parent" />
    <component :is="p.icon" :size="14" />
    {{ p.siteLabel }}
    <v-icon size="12" class="up-ext" aria-hidden="true">mdi-open-in-new</v-icon>
  </a>

  <div v-else class="up-strip" :data-testid="`upstream-get-${program}`">
    <span class="up-mark"><component :is="p.icon" :size="28" /></span>
    <div class="up-text">
      <p class="up-title">Don't have {{ p.name }} yet?</p>
      <p class="up-about">{{ credit }} {{ p.about }}</p>
    </div>
    <a
      class="up-get"
      :href="p.get"
      target="_blank"
      rel="noopener noreferrer"
    >
      Get {{ p.name }}
      <v-icon size="14" class="up-ext" aria-hidden="true">mdi-open-in-new</v-icon>
    </a>
  </div>
</template>

<style scoped>
/* The SettingsInfoCard ground, so it reads as help inside the card rather
   than a second card. */
.up-strip {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
}

.up-mark {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
}

.up-text p {
  margin: 0;
}

.up-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.up-text .up-about {
  margin-top: var(--space-1);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* AppButton's outline variant, on a link: a website is never the key action. */
.up-get,
.up-link {
  display: inline-flex;
  align-items: center;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font-family: var(--font-ui);
  font-weight: var(--weight-medium);
  white-space: nowrap;
  text-decoration: none;
  transition: background var(--dur-1) var(--ease-standard);
}

.up-get {
  gap: var(--space-2);
  height: var(--control-h);
  padding: 0 var(--space-5);
  font-size: var(--text-base);
  color: rgb(var(--v-theme-on-surface));
  border-color: rgb(var(--v-theme-border));
}

/* AppButton's small ghost: recedes until hovered. */
.up-link {
  gap: var(--space-3);
  height: var(--control-h-sm);
  padding: 0 var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.up-get:hover,
.up-link:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.up-ext {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
