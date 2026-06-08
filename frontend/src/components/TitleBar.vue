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

const props = defineProps({
  installType: { type: String, default: "pip" },
  checkForUpdates: { type: Boolean, default: null },
});

const desktop = typeof window !== "undefined" ? window.pixlstashDesktop : null;
const isMac = /mac/i.test(
  (typeof navigator !== "undefined" && (navigator.platform || navigator.userAgent)) || "",
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
      <span class="titlebar-name"
        >Pixl<span class="titlebar-name-accent">Stash</span></span
      >
      <span class="titlebar-version">v{{ appVersion }}</span>
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
    <div
      v-if="updateAvailable && !updateDismissed"
      class="titlebar-update"
    >
      <a
        :href="latestVersionUrl"
        target="_blank"
        rel="noopener noreferrer"
        :class="securityUpdateClass"
        :title="securityUpdateTitle"
        >&#x2191; v{{ latestVersion
        }}{{
          latestSecurityLevel ? " security ⚠️" : " available"
        }}</a
      ><button
        class="titlebar-update-dismiss"
        :title="`Dismiss v${latestVersion} update alert`"
        @click.prevent="dismissUpdateAlert"
      >
        &times;
      </button>
    </div>
    <div v-if="!isMac" class="titlebar-controls">
      <button class="tb-btn" type="button" aria-label="Minimize" @click="minimize">
        <svg width="10" height="10" viewBox="0 0 10 10">
          <rect x="0" y="4.5" width="10" height="1" fill="currentColor" />
        </svg>
      </button>
      <button class="tb-btn" type="button" aria-label="Maximize" @click="toggleMaximize">
        <svg width="10" height="10" viewBox="0 0 10 10">
          <rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" />
        </svg>
      </button>
      <button class="tb-btn tb-close" type="button" aria-label="Close" @click="close">
        <svg width="10" height="10" viewBox="0 0 10 10">
          <path d="M1,1 L9,9 M9,1 L1,9" stroke="currentColor" stroke-width="1.1" />
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.titlebar {
  display: flex;
  align-items: center;
  height: 34px;
  flex-shrink: 0;
  /* Match the toolbar below (Toolbar.vue uses --v-theme-background), not the
     sidebar, so the title bar and toolbar read as one continuous strip. Note
     --v-theme-toolbar equals --v-theme-sidebar, hence the dedicated var here. */
  background: rgb(var(--v-theme-background));
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
  width: 20px;
  height: 20px;
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
  font-weight: 600;
  letter-spacing: 0.4px;
  flex-shrink: 0;
}

.titlebar-name-accent {
  color: rgb(var(--v-theme-accent));
}

.titlebar-version {
  opacity: 0.55;
  font-size: 11px;
  flex-shrink: 0;
}

/* Breadcrumb: current-view path, inline after the version. */
.titlebar-breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  font-weight: 600;
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
  font-size: 11px;
  line-height: 1;
  color: rgba(var(--v-theme-accent), 0.95);
  text-decoration: none;
  white-space: nowrap;
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
