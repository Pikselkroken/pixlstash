<script setup>
// The app's title bar, in the browser and in the Electron desktop shell alike
// (#2158): logo, the library picker, the workspace switch centred, then the
// tasks pill, Settings and, on the desktop, the window controls. It is the
// same strip in every workspace, so switching Media | Workflows | Models
// changes which segment is lit and nothing else - no control here may size
// itself from the route.
//
// The desktop shell is frameless, so here the strip is also the window's drag
// region and draws min/maximize/close on non-mac platforms (macOS keeps its
// native traffic lights). All desktop calls go through
// `window.pixlstashDesktop?.…`, so they no-op in a browser.
import { computed, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { useVersionCheck } from "../composables/useVersionCheck";
import { MODEL_SHELF_ROUTES, WORKFLOW_ROUTES } from "../router/routeNames";
import { useModelShelfStore } from "../stores/useModelShelfStore";
import { useUserPrefsStore } from "../stores/useUserPrefsStore";
import { isReadOnly } from "../utils/apiClient";
import TitleBarLibraryPicker from "./TitleBarLibraryPicker.vue";
import TitleBarTasks from "./TitleBarTasks.vue";
import WordmarkLogo from "./WordmarkLogo.vue";
import AppBarButton from "./widgets/AppBarButton.vue";
import Tooltip from "./widgets/Tooltip.vue";

const props = defineProps({
  installType: { type: String, default: "pip" },
  checkForUpdates: { type: Boolean, default: null },
  // Empty for a share/read-only session, which gets neither the name nor the
  // picker: the registry is owner-only.
  activeLibraryName: { type: String, default: "" },
});

const emit = defineEmits(["open-settings"]);

const desktop = typeof window !== "undefined" ? window.pixlstashDesktop : null;
const isMac = /mac/i.test(
  (typeof navigator !== "undefined" &&
    (navigator.platform || navigator.userAgent)) ||
    "",
);

const route = useRoute();
const modelShelfStore = useModelShelfStore();
const userPrefsStore = useUserPrefsStore();

// Every route outside the workflow library and the model shelf is Media: the
// grid, a set, a person, the Scrapheap, Duplicates, Insights, Moves.
const currentWorkspace = computed(() =>
  WORKFLOW_ROUTES.includes(route.name)
    ? "workflows"
    : MODEL_SHELF_ROUTES.includes(route.name)
      ? "models"
      : "media",
);

// Where Media and Models were left, so switching back lands on the sidebar
// row you were on (Favourites in a project, a set, Duplicates, the shelf's
// Runs) rather than on the workspace's front page. Overlays over the grid -
// the lightbox with its one-close `?from=`, and a tag review - are not where
// you were. Models keeps its row, the training run open on it (`?run=`,
// `?folder-run=`) and the share `?token=`, but not `?model=`, a one-shot jump
// that would re-select that model on return. Workflows needs nothing, its
// places live in its store.
const OVERLAY_QUERY = [
  "overlay",
  "from",
  "review",
  "review_project",
  "review_set",
  "review_character",
];
const MODELS_KEPT_QUERY = ["run", "folder-run", "token"];
const leftAt = ref({});
watch(
  () => route.fullPath,
  () => {
    if (currentWorkspace.value === "media") {
      const query = { ...route.query };
      for (const key of OVERLAY_QUERY) delete query[key];
      leftAt.value.media = { path: route.path, query };
    } else if (currentWorkspace.value === "models") {
      const query = {};
      for (const key of MODELS_KEPT_QUERY) {
        if (key in route.query) query[key] = route.query[key];
      }
      leftAt.value.models = { path: route.path, query };
    }
  },
  { immediate: true },
);

// The front-page paths are the ones these places already had, so a bookmark
// still lands. A share session's credential lives in `?token=` and only boot
// reads it, so a link without it would open a session that 401s on reload or
// in a new tab; a remembered Media route carries its own.
const workspaces = computed(() => {
  const token = route.query.token;
  const at = (path) => (token ? { path, query: { token } } : path);
  const current = currentWorkspace.value;
  return [
    { id: "media", label: "Media", to: leftAt.value.media ?? at("/") },
    {
      id: "workflows",
      label: "Workflows",
      to: at("/workflows"),
      // Every /workflows route is owner-only. Shown and inert rather than
      // hidden, as the sidebar row was: the demo site is a READ session, and
      // hiding a workspace there advertises a smaller product (#1014).
      locked: isReadOnly.value
        ? "The workflow library is only available in your own library"
        : "",
    },
    {
      id: "models",
      label: "Models",
      to: leftAt.value.models ?? at("/models"),
      locked: isReadOnly.value
        ? "The model shelf is only available in your own library"
        : "",
      // Models arrived from the Downloads folder while the shelf was not
      // open. The sidebar clears it when the shelf opens.
      news: modelShelfStore.unseenArrivals && !isReadOnly.value,
    },
  ].map((workspace) => ({ ...workspace, current: workspace.id === current }));
});

/** The current segment stays put: it names where you are, it is not a reset. */
function follow(workspace, navigate, event) {
  if (workspace.current) {
    event.preventDefault();
    return;
  }
  navigate(event);
}

// The title bar owns the update check in both shells, so it runs once.
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
  true,
);

const securityUpdateClass = computed(() => {
  if (!latestSecurityLevel.value) return "titlebar-update-link";
  return isHighSecurity.value
    ? "titlebar-update-link titlebar-update-security titlebar-update-security--high"
    : "titlebar-update-link titlebar-update-security";
});

const telemetryIndicatorTitle = computed(() => {
  const active = [];
  if (userPrefsStore.checkForUpdates) active.push("update checks");
  if (userPrefsStore.telemetrySendInstallId)
    active.push("an anonymous install ID");
  if (userPrefsStore.telemetrySendFeatureUsage) active.push("feature usage");
  if (userPrefsStore.telemetrySendErrorReports) active.push("error reports");
  if (userPrefsStore.telemetrySendHardwareProfile)
    active.push("a hardware profile");
  if (!active.length) return "Telemetry is off";
  if (active.length === 1) return `Sending ${active[0]}`;
  const last = active.pop();
  return `Sending ${active.join(", ")} and ${last}`;
});

const minimize = () => desktop?.windowMinimize?.();
const toggleMaximize = () => desktop?.windowToggleMaximize?.();
const close = () => desktop?.windowClose?.();
</script>

<template>
  <header
    class="titlebar"
    :class="{ 'titlebar--desktop': desktop, 'titlebar--mac': desktop && isMac }"
  >
    <div class="titlebar-start">
      <a
        href="https://pikselkroken.github.io/pixlstash/"
        target="_blank"
        rel="noopener noreferrer"
        class="titlebar-logo-link"
      >
        <img src="/Logo.png" alt="PixlStash logo" class="titlebar-logo" />
      </a>
      <WordmarkLogo class="titlebar-name" />
      <button
        v-if="userPrefsStore.telemetryActive"
        type="button"
        class="titlebar-telemetry-dot"
        :aria-label="`${telemetryIndicatorTitle}. Open privacy settings.`"
        @click="emit('open-settings', 'privacy')"
      >
        <Tooltip
          :text="telemetryIndicatorTitle"
          activator="parent"
          :describe="false"
        />
      </button>
      <template v-if="activeLibraryName">
        <span class="titlebar-divider" aria-hidden="true"></span>
        <TitleBarLibraryPicker
          :active-library-name="activeLibraryName"
          @open-settings="(tab) => emit('open-settings', tab)"
        />
      </template>
    </div>

    <nav class="titlebar-workspaces" aria-label="Workspace">
      <template v-for="workspace in workspaces" :key="workspace.id">
        <!-- Locked: a button, so `aria-disabled` reaches the accessibility
             tree as a disabled state and the segment stays tabbable for its
             explanation. A link has no disabled state to carry it. -->
        <button
          v-if="workspace.locked"
          type="button"
          class="titlebar-ws titlebar-ws--locked"
          aria-disabled="true"
        >
          <Tooltip :text="workspace.locked" activator="parent" />
          {{ workspace.label }}
        </button>
        <RouterLink
          v-else
          v-slot="{ href, navigate }"
          :to="workspace.to"
          custom
        >
          <a
            :href="href"
            class="titlebar-ws"
            :class="{ 'titlebar-ws--current': workspace.current }"
            :aria-current="workspace.current ? 'page' : undefined"
            :data-workspace="workspace.id"
            @click="follow(workspace, navigate, $event)"
          >
            {{ workspace.label }}
            <span
              v-if="workspace.news"
              class="titlebar-ws-news"
              aria-hidden="true"
            ></span>
            <span v-if="workspace.news" class="visually-hidden"
              >, new models arrived</span
            >
          </a>
        </RouterLink>
      </template>
    </nav>

    <div class="titlebar-end">
      <div class="titlebar-drag" @dblclick="toggleMaximize"></div>
      <div v-if="updateAvailable && !updateDismissed" class="titlebar-update">
        <a
          :href="latestVersionUrl"
          target="_blank"
          rel="noopener noreferrer"
          :class="securityUpdateClass"
          ><Tooltip
            :text="securityUpdateTitle || ''"
            activator="parent"
          />&#x2191; v{{ latestVersion
          }}{{ latestSecurityLevel ? " security" : " available"
          }}<span
            v-if="latestSecurityLevel"
            class="titlebar-update-warn"
            aria-hidden="true"
            >⚠️</span
          ></a
        >
        <AppBarButton
          icon="close"
          aria-label="Dismiss update alert"
          :tooltip="`Dismiss v${latestVersion} update alert`"
          @click.prevent="dismissUpdateAlert"
        />
      </div>
      <TitleBarTasks v-if="!isReadOnly" />
      <AppBarButton
        class="titlebar-settings"
        icon="cog-outline"
        tooltip="Settings"
        @click="emit('open-settings')"
      />
      <div v-if="desktop && !isMac" class="titlebar-controls">
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
  </header>
</template>

<style scoped>
.titlebar {
  /* Three columns, the outer two equal: the switch sits at the true centre
     whatever the library is called, so nothing it holds can move it. The end
     column will not shrink below its content (the window controls must stay
     on screen); the start column gives way first and the library name
     truncates. */
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(max-content, 1fr);
  align-items: center;
  /* MUST stay in sync with --titlebar-h in style.css (the reserved strip every
     full-screen overlay anchors its top at). If you change this, change that.
     The switch's track is --control-h-bar, with --space-2 above and below. */
  height: 40px;
  flex-shrink: 0;
  box-sizing: border-box;
  /* Positioned + the `--z-titlebar` rung so the title bar (and its drag region
     + window controls) is NEVER covered by an in-app overlay: occluding it
     costs the user the ability to move or close the window. The title bar is a
     child of .app-viewport alongside the in-app overlays, and .app-viewport is
     `position: fixed; z-index: 0`, so this rung competes with exactly those
     siblings. It sits ABOVE `--z-modal` (4000) on purpose - the title bar is
     the FIRST child of .app-viewport and ReviewSessionsOverlay (its own 4000
     stacking context, with `inset: 0` sub-scrims) is a later one, so a tie at
     4000 would be resolved by DOM order against the strip. It sits BELOW
     `--z-notice` (5000) so an error notice is still readable over the chrome.
     (Vuetify dialogs/overlays teleport to <body> at ~2000 and so live outside
     this stacking context; they are kept off the strip by anchoring their top
     at var(--titlebar-h) instead, not by this z-index.) */
  position: relative;
  z-index: var(--z-titlebar);
  border-bottom: 1px solid rgba(var(--v-theme-on-background), 0.12);
  /* Paint from the `toolbar` token so the title bar and the toolbar strip below it
     read as one continuous piece. Both now track `toolbar`, which the theme can set
     equal to or distinct from `background` (the grid canvas) and `sidebar`. */
  background: rgb(var(--v-theme-toolbar));
  color: rgb(var(--v-theme-on-background));
  user-select: none;
}

/* The frameless window moves by this strip; every control in it opts out. */
.titlebar--desktop {
  -webkit-app-region: drag;
}

.titlebar--desktop :is(a, button, nav) {
  -webkit-app-region: no-drag;
}

/* Electron never delivers a click on a drag region to the page, so an open
   Vuetify menu (the library picker, the tasks popover, any v-menu elsewhere)
   would not see one there as a click outside and would stay open. Give the
   strip back to the page while one is up: the click lands and closes it. */
:root:has(.v-menu.v-overlay--active) .titlebar--desktop {
  -webkit-app-region: no-drag;
}

.titlebar-start {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  padding: 0 var(--space-4);
}

/* Leave room for the native traffic lights on macOS. */
.titlebar--mac .titlebar-start {
  padding-left: 78px;
}

.titlebar-end {
  display: flex;
  align-items: center;
  align-self: stretch;
  gap: var(--space-2);
  min-width: 0;
  padding-left: var(--space-4);
}

/* No window controls in a browser, so the last control keeps a margin. */
.titlebar:not(.titlebar--desktop) .titlebar-end,
.titlebar--mac .titlebar-end {
  padding-right: var(--space-3);
}

.titlebar-logo-link {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  border-radius: var(--radius-sm);
}

.titlebar-logo {
  /* Sized to sit close to the wordmark's letter height rather than towering
     over it. The icon is the largest element by a hair, as the brand anchor,
     but reads as part of the same group. */
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
  /* Tiny5 brand wordmark (WordmarkLogo.vue), sized by font-size. "Pixl"
     tracks the title-bar text colour; "Stash" is the accent, the same two-tone
     split the login screen and website use (DESIGN.md, "Wordmark amber"). */
  font-size: var(--text-lg);
  flex-shrink: 0;
  color: rgb(var(--v-theme-on-background));
  --wordmark-accent: rgb(var(--v-theme-accent));
}

/* Telemetry is on: an amber dot beside the wordmark that opens Privacy. */
.titlebar-telemetry-dot {
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-accent));
  padding: 0;
  flex-shrink: 0;
}

.titlebar-divider {
  width: 1px;
  height: 20px;
  flex-shrink: 0;
  background: rgb(var(--v-theme-divider));
}

/* ── Workspace switch ─────────────────────────────────────────────────────
   The Segmented track (widgets/Segmented.vue), but a nav of links: it goes
   somewhere rather than setting a value. Every segment is one fixed width, so
   the bold current label cannot widen its segment and nudge the others. The
   current one is a selection, so it takes the olive wash with a hairline ring
   (as the active sidebar row does), never the solid primary fill. */
.titlebar-workspaces {
  display: flex;
  gap: var(--space-1);
  box-sizing: border-box;
  height: var(--control-h-bar);
  padding: var(--space-2);
  background: var(--track-trough);
  /* Inset, so the track stays 32px. A 1px border would make it 34. */
  box-shadow: inset 0 0 0 1px var(--track-ring);
  border-radius: var(--radius-md);
}

.titlebar-ws {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 96px;
  height: var(--control-h-sm);
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-regular);
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background var(--dur-1) var(--ease-standard),
    color var(--dur-1) var(--ease-standard);
}

.titlebar-ws:not(.titlebar-ws--current, .titlebar-ws--locked):hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-background));
}

.titlebar-ws--current {
  background: var(--active-wash);
  box-shadow: inset 0 0 0 1px var(--active-bar);
  color: var(--active-text);
  font-weight: var(--weight-semibold);
  cursor: default;
}

/* Visible and tabbable for its explanation, but offers no response. */
.titlebar-ws--locked {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

/* "New models arrived": the attention dot, amber because it asks for a look. */
.titlebar-ws-news {
  position: absolute;
  top: var(--space-2);
  right: var(--space-3);
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-accent));
}

.titlebar-drag {
  flex: 1;
  align-self: stretch;
  min-width: var(--space-6);
}

.titlebar-update {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  white-space: nowrap;
}

.titlebar-update-link {
  display: inline-flex;
  align-items: center;
  font-size: var(--text-2xs);
  line-height: 1;
  color: rgb(var(--v-theme-on-background));
  text-decoration: none;
  white-space: nowrap;
}

/* Isolate the ⚠️ emoji in its own centered box so its tall glyph metrics don't
   baseline-shift the update text. */
.titlebar-update-warn {
  display: inline-flex;
  align-items: center;
  margin-left: var(--space-2);
  line-height: 1;
}

.titlebar-update-link:hover {
  text-decoration: underline;
}

.titlebar-update-security {
  color: rgb(var(--v-theme-surface-warning));
}

.titlebar-update-security:hover {
  color: rgba(var(--v-theme-surface-warning), 0.85);
}

.titlebar-update-security--high {
  color: rgb(var(--v-theme-surface-error));
}

.titlebar-update-security--high:hover {
  color: rgba(var(--v-theme-surface-error), 0.85);
}

.titlebar-controls {
  display: flex;
  align-self: stretch;
}

.tb-btn {
  width: 46px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: inherit;
  opacity: 0.8;
  cursor: default;
}

.tb-btn:hover {
  background: var(--hover-wash);
  opacity: 1;
}

.tb-close:hover {
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  opacity: 1;
}

/* A narrow window: the wordmark and the update line yield before the switch
   is pushed off centre or the library name is squeezed to a sliver. */
@media (max-width: 1000px) {
  .titlebar-name {
    display: none;
  }
}

@media (max-width: 850px) {
  .titlebar-update {
    display: none;
  }
}
</style>
