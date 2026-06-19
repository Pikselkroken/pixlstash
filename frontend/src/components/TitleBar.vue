<script setup>
// Custom window title bar for the Electron desktop shell. Renders nothing in a
// plain browser. It uses the toolbar colour so it reads as one continuous strip
// with the app toolbar below it, is a drag region for moving the (frameless)
// window, and draws min/maximize/close controls on non-mac platforms (macOS
// keeps its native traffic lights).
//
// In the desktop shell it also hosts the app branding: logo + version +
// breadcrumb on the left and the "new version available" alert on the right.
// The browser keeps those in the sidebar/grid instead (this component is empty
// there), so the sidebar and in-grid copies are gated on !window.pixlstashDesktop.
import { computed } from "vue";
import { useBreadcrumb } from "../composables/useBreadcrumb";
import { useVersionCheck } from "../composables/useVersionCheck";
import WordmarkLogo from "./WordmarkLogo.vue";

const props = defineProps({
  installType: { type: String, default: "pip" },
  checkForUpdates: { type: Boolean, default: null },
});

const desktop = typeof window !== "undefined" ? window.pixlstashDesktop : null;
const isMac = /mac/i.test(
  (typeof navigator !== "undefined" &&
    (navigator.platform || navigator.userAgent)) ||
    "",
);

const appVersion = __APP_VERSION__;

const { breadcrumb, navigateBreadcrumb } = useBreadcrumb();

// The title bar owns the update check on the desktop; the sidebar owns it in
// the browser. `enabled` (desktop only) keeps the fetch from running twice.
const {
  latestVersion,
  latestVersionUrl,
  latestSecurityLevel,
  updateAvailable,
  updateDismissed,
  isHighSecurity,
  securityUpdateTitle,
  dismissUpdateAlert,
} = useVersionCheck(
  () => props.installType,
  () => props.checkForUpdates,
  Boolean(desktop),
);

const securityUpdateClass = computed(() => {
  if (!latestSecurityLevel.value) return "titlebar-update-link";
  return isHighSecurity.value
    ? "titlebar-update-link titlebar-update-security titlebar-update-security--high"
    : "titlebar-update-link titlebar-update-security";
});

const minimize = () => desktop?.windowMinimize?.();
const toggleMaximize = () => desktop?.windowToggleMaximize?.();
const close = () => desktop?.windowClose?.();
</script>

<template>
  <div v-if="desktop" class="titlebar" :class="{ 'titlebar--mac': isMac }">
    <div class="titlebar-brand">
      <a
        href="https://pikselkroken.github.io/pixlstash/"
        target="_blank"
        rel="noopener noreferrer"
        class="titlebar-logo-link"
      >
        <img src="/Logo.png" alt="PixlStash logo" class="titlebar-logo" />
      </a>
      <WordmarkLogo class="titlebar-name" />
      <nav
        v-if="breadcrumb.length"
        class="titlebar-breadcrumb"
        aria-label="Current view"
      >
        <span class="titlebar-bc-sep" aria-hidden="true">›</span>
        <template v-for="(crumb, i) in breadcrumb" :key="i">
          <span v-if="i > 0" class="titlebar-bc-sep" aria-hidden="true">›</span>
          <button
            v-if="crumb.to"
            type="button"
            class="titlebar-bc-crumb is-link"
            :title="`Go to ${crumb.label}`"
            @click="navigateBreadcrumb(crumb)"
          >
            {{ crumb.label }}
          </button>
          <span v-else class="titlebar-bc-crumb" :title="crumb.label">{{
            crumb.label
          }}</span>
        </template>
      </nav>
    </div>
    <div class="titlebar-drag" @dblclick="toggleMaximize"></div>
    <span class="titlebar-version">v{{ appVersion }}</span>
    <div v-if="updateAvailable && !updateDismissed" class="titlebar-update">
      <a
        :href="latestVersionUrl"
        target="_blank"
        rel="noopener noreferrer"
        :class="securityUpdateClass"
        :title="securityUpdateTitle"
        >&#x2191; v{{ latestVersion
        }}{{ latestSecurityLevel ? " security" : " available"
        }}<span
          v-if="latestSecurityLevel"
          class="titlebar-update-warn"
          aria-hidden="true"
        >⚠️</span></a
      ><button
        type="button"
        class="titlebar-update-dismiss"
        aria-label="Dismiss update alert"
        :title="`Dismiss v${latestVersion} update alert`"
        @click.prevent="dismissUpdateAlert"
      >
        &times;
      </button>
    </div>
    <div v-if="!isMac" class="titlebar-controls">
      <button
        class="tb-btn"
        type="button"
        aria-label="Minimize"
        @click="minimize"
      >
        <svg width="10" height="10" viewBox="0 0 10 10">
          <rect x="0" y="4.5" width="10" height="1" fill="currentColor" />
        </svg>
      </button>
      <button
        class="tb-btn"
        type="button"
        aria-label="Maximize"
        @click="toggleMaximize"
      >
        <svg width="10" height="10" viewBox="0 0 10 10">
          <rect
            x="0.5"
            y="0.5"
            width="9"
            height="9"
            fill="none"
            stroke="currentColor"
          />
        </svg>
      </button>
      <button
        class="tb-btn tb-close"
        type="button"
        aria-label="Close"
        @click="close"
      >
        <svg width="10" height="10" viewBox="0 0 10 10">
          <path
            d="M1,1 L9,9 M9,1 L1,9"
            stroke="currentColor"
            stroke-width="1.1"
          />
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.titlebar {
  display: flex;
  align-items: center;
  /* MUST stay in sync with --titlebar-h in style.css (the reserved strip every
     full-screen overlay anchors its top at). If you change this, change that. */
  height: 34px;
  flex-shrink: 0;
  box-sizing: border-box;
  /* Positioned + a z-index above every overlay in the app so the title bar (and
     its drag region + window controls) is NEVER covered. The title bar is a
     child of .app-viewport alongside the in-app overlays, so this wins over all
     of them. z-index audit (highest overlays in the app): import-progress modal
     99999, autocomplete dropdowns / ref-preview 9999, rf-zoom 4100, image
     overlay 1000. 100000 must stay above all of them — bump it if any overlay
     ever goes higher. (Vuetify dialogs/overlays teleport to <body> at ~2000 and
     so live outside this stacking context; they are kept off the strip by
     anchoring their top at var(--titlebar-h) instead, not by this z-index.) */
  position: relative;
  z-index: 100000;
  border-bottom: 1px solid rgba(var(--v-theme-on-background), 0.12);
  /* Paint from the `toolbar` token so the title bar and the toolbar strip below it
     read as one continuous piece. Both now track `toolbar`, which the theme can set
     equal to or distinct from `background` (the grid canvas) and `sidebar`. */
  background: rgb(var(--v-theme-toolbar));
  color: rgb(var(--v-theme-on-background));
  -webkit-app-region: drag;
  user-select: none;
}

.titlebar-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 0 12px;
  font-size: 12px;
}

/* Leave room for the native traffic lights on macOS. */
.titlebar--mac .titlebar-brand {
  padding-left: 78px;
}

.titlebar-logo-link {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  border-radius: 4px;
  outline: none;
  -webkit-app-region: no-drag;
}

.titlebar-logo {
  /* Sized to sit close to the wordmark/breadcrumb letter height rather than
     towering over them. The icon is the largest element by a hair, as the brand
     anchor, but reads as part of the same group. */
  width: 16px;
  height: 16px;
  object-fit: contain;
  transition:
    filter 0.2s ease,
    transform 0.2s ease;
}

.titlebar-logo-link:hover .titlebar-logo {
  filter: drop-shadow(0 0 6px rgba(var(--v-theme-accent), 0.9));
  transform: scale(1.08);
}

.titlebar-name {
  /* Tiny5 brand wordmark (WordmarkLogo.vue), sized by font-size. */
  font-size: 16px;
  flex-shrink: 0;
  /* "Pixl" tracks the title-bar text colour; "Stash" is the subdued accent blend. */
  color: rgb(var(--v-theme-on-background));
  --wordmark-accent: color-mix(
    in srgb,
    rgb(var(--v-theme-accent)) 55%,
    rgb(var(--v-theme-on-background))
  );
}

.titlebar-version {
  display: inline-flex;
  align-items: center;
  line-height: 1;
  opacity: 0.55;
  font-size: 11px;
  flex-shrink: 0;
  padding: 0 12px;
}

/* Breadcrumb: current-view path, inline after the version. */
.titlebar-breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  /* Sized so the sans cap-height lands close to the pixel wordmark's glyph
     height (the two fonts render very differently at the same px), and weight
     500 keeps it from out-shouting the brand. */
  font-size: 14px;
  line-height: 1;
  font-weight: 500;
  /* Match the wordmark's downward nudge (relative positioning, not transform, so
     the text isn't layerized and resampled) so the whole group sits on the icon's
     optical centre rather than above it. */
  position: relative;
  top: 1px;
}

.titlebar-bc-sep {
  flex: 0 0 auto;
  opacity: 0.4;
}

.titlebar-bc-crumb {
  margin: 0;
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  color: inherit;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  opacity: 0.85;
}

.titlebar-bc-crumb.is-link {
  cursor: pointer;
  color: rgb(var(--v-theme-primary));
  opacity: 1;
  -webkit-app-region: no-drag;
}

.titlebar-bc-crumb.is-link:hover {
  text-decoration: underline;
}

.titlebar-drag {
  flex: 1;
  align-self: stretch;
  min-width: 24px;
}

.titlebar-update {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 10px;
  white-space: nowrap;
  -webkit-app-region: no-drag;
}

.titlebar-update-link {
  display: inline-flex;
  align-items: center;
  font-size: 11px;
  line-height: 1;
  color: rgba(var(--v-theme-accent), 0.95);
  text-decoration: none;
  white-space: nowrap;
}

/* Isolate the ⚠️ emoji in its own centered box so its tall glyph metrics don't
   baseline-shift the update text (and knock it off-centre vs the version). */
.titlebar-update-warn {
  display: inline-flex;
  align-items: center;
  margin-left: 4px;
  line-height: 1;
}

.titlebar-update-link:hover {
  text-decoration: underline;
}

.titlebar-update-security {
  color: #e57c00;
}

.titlebar-update-security:hover {
  color: #c96000;
}

.titlebar-update-security--high {
  color: #e53935;
}

.titlebar-update-security--high:hover {
  color: #c62828;
}

.titlebar-update-dismiss {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  width: 12px;
  height: 12px;
  font-size: 0.7rem;
  line-height: 1;
  background: transparent;
  border: none;
  cursor: pointer;
  color: rgba(var(--v-theme-on-background), 0.5);
}

.titlebar-update-dismiss:hover {
  color: rgba(var(--v-theme-on-background), 0.9);
}

.titlebar-controls {
  display: flex;
  align-self: stretch;
  -webkit-app-region: no-drag;
}

.tb-btn {
  width: 46px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  color: inherit;
  opacity: 0.8;
  cursor: default;
}

.tb-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.1);
  opacity: 1;
}

.tb-close:hover {
  background: #e81123;
  color: #fff;
  opacity: 1;
}
</style>
