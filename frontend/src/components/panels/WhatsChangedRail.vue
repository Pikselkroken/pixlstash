<script setup>
// The grid inspector's "What's changed" tab (#2145): the five gestures 2.0
// moved, as things to do. Doing one on a real picture ticks it (ImageGrid and
// Lightbox call `whatsChanged.tick`), and the step just done explains what
// happened before it offers the choice, so nobody rejects a change unfelt.
import { computed, nextTick } from "vue";
import { VIcon } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import AppInspector from "../widgets/AppInspector.vue";
import Segmented from "../widgets/Segmented.vue";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import {
  INSPECTOR_BAND,
  STEP_IDS,
  useWhatsChangedStore,
} from "../../stores/useWhatsChangedStore";

const RELEASE_NOTES_URL = "https://github.com/Pikselkroken/pixlstash/releases";

const whatsChanged = useWhatsChangedStore();
const sidebarStore = useSidebarStore();
const userPrefs = useUserPrefsStore();

const OPEN_OPTIONS = [
  { id: "double", label: "Double click" },
  { id: "single", label: "Single click" },
];
const WHEEL_OPTIONS = [
  { id: "zoom", label: "Zoom" },
  { id: "browse", label: "Browse pictures" },
];

const STEPS = {
  click: {
    title: "Click a picture",
    todo: "One click selects it now. Double-click opens it.",
    said: () =>
      userPrefs.gridOpenGesture === "single"
        ? "That click opened it, the 1.x way: Single click is on."
        : "That click selected it. In 1.x it would have opened it. Double-click opens now.",
    choice: {
      label: "Open pictures with",
      options: OPEN_OPTIONS,
      get: () => userPrefs.gridOpenGesture,
      set: (id) => (userPrefs.gridOpenGesture = id),
    },
  },
  close: {
    title: "Open one, then click the dark area",
    todo: "That closes the viewer. So does a middle-click anywhere, and Esc.",
    said: () =>
      "That closed the viewer. A middle-click anywhere does the same, and Esc still works.",
  },
  wheel: {
    title: "Turn the wheel in the viewer",
    todo: "It zooms. You can make it step through pictures instead.",
    said: () =>
      "Ctrl+wheel always zooms and Shift+wheel always steps, whichever you pick here.",
    choice: {
      label: "Mouse wheel in the viewer",
      options: WHEEL_OPTIONS,
      get: () => userPrefs.viewerWheelAction,
      set: (id) => (userPrefs.viewerWheelAction = id),
    },
  },
  menu: {
    title: "Right-click a picture",
    todo: "The menu is half as tall; tools are under Analyse, Generate, Adjust and Find & compare.",
    said: () =>
      "Tools now sit under Analyse, Generate, Adjust and Find & compare.",
  },
  drop: {
    title: "Drop a picture onto the grid",
    todo: "It lands at the top as a tile. The upload dialog is gone.",
    said: () =>
      "It landed at the top of the grid as a tile, with its progress on it.",
  },
};

const steps = computed(() =>
  STEP_IDS.map((id) => ({
    id,
    ...STEPS[id],
    done: whatsChanged.done.includes(id),
    open: whatsChanged.openStep === id && whatsChanged.done.includes(id),
  })),
);
const doneCount = computed(() => whatsChanged.done.length);

/** Where you land after leaving: the inspector stays open, on Details. */
function leave() {
  whatsChanged.dismissChecklist();
  nextTick(() =>
    document
      .querySelector(
        ".stats-sidebar .inspector-tab--active, .stats-sidebar .rail-clear",
      )
      ?.focus(),
  );
}

/** A done step reopens its explanation (and its choice) on a click. */
function openStep(step) {
  if (step.done) whatsChanged.openStep = step.id;
}
</script>

<template>
  <AppInspector
    class="whats-changed"
    label="Inspector"
    :open="sidebarStore.statsOpen"
    :band="INSPECTOR_BAND"
    band-value="changes"
    @update:band-value="whatsChanged.showTab($event)"
    @close-band="leave"
  >
    <div class="wc-head">
      <h2 class="wc-title">Five things work differently</h2>
      <p class="wc-sub" aria-live="polite">
        {{
          whatsChanged.allDone
            ? "That's all of them. This tab will see itself out."
            : "Try each one on your own pictures. It ticks itself off when you do."
        }}
      </p>
      <div class="wc-prog">
        <span class="wc-track"
          ><i :style="{ width: `${(doneCount / STEP_IDS.length) * 100}%` }"></i
        ></span>
        <span class="num">{{ doneCount }} of {{ STEP_IDS.length }}</span>
      </div>
      <div class="wc-scram">
        <span id="wc-scram-q" class="wc-scram__q"
          ><b>Not your first rodeo?</b>Bin the list. You can fetch it
          back.</span
        >
        <AppButton
          icon-left="hand-wave"
          aria-describedby="wc-scram-q"
          @click="leave"
          >Get outta here</AppButton
        >
      </div>
    </div>
    <ol class="wc-steps">
      <li
        v-for="step in steps"
        :key="step.id"
        class="wc-step"
        :class="{ 'wc-step--done': step.done, 'wc-step--open': step.open }"
        :data-step="step.id"
      >
        <span class="wc-cb" :class="{ 'wc-cb--on': step.done }">
          <v-icon v-if="step.done" size="13">mdi-check</v-icon>
          <span class="visually-hidden">{{
            step.done ? "Done:" : "To do:"
          }}</span>
        </span>
        <div>
          <h3 class="wc-step__title">
            <button
              v-if="step.done && !step.open"
              type="button"
              class="wc-step__reopen"
              @click="openStep(step)"
            >
              {{ step.title }}
            </button>
            <template v-else>{{ step.title }}</template>
          </h3>
          <template v-if="step.open">
            <p class="wc-step__said">{{ step.said() }}</p>
            <div v-if="step.choice" class="wc-ctl">
              <span class="wc-ctl__label">{{ step.choice.label }}</span>
              <Segmented
                full
                :model-value="step.choice.get()"
                :options="step.choice.options"
                :aria-label="step.choice.label"
                @update:model-value="step.choice.set"
              />
            </div>
          </template>
          <p v-else-if="!step.done" class="wc-step__todo">{{ step.todo }}</p>
        </div>
      </li>
    </ol>
    <template #footer>
      <div class="wc-foot">
        <a
          class="wc-link"
          :href="RELEASE_NOTES_URL"
          target="_blank"
          rel="noopener noreferrer"
          >All release notes <v-icon size="16">mdi-open-in-new</v-icon></a
        >
      </div>
    </template>
  </AppInspector>
</template>

<style scoped>
.wc-head {
  display: flex;
  flex-direction: column;
  padding: var(--space-2) var(--space-1) var(--space-2);
}

.wc-title {
  margin: 0 0 var(--space-2);
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
}

.wc-sub {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wc-prog {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-4);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* A count, not a selection: the teal identity hue. */
.wc-track {
  flex: 1;
  height: 4px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.1);
  overflow: hidden;
}

.wc-track i {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-tertiary));
  transition: width var(--dur-2) var(--ease-standard);
}

/* The way out, as an aside rather than part of the list. */
.wc-scram {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--space-2);
  margin-top: var(--space-4);
  padding: var(--space-3);
  border: 1px dashed rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.wc-scram__q {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wc-scram__q b {
  display: block;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

.wc-steps {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.wc-step {
  display: grid;
  grid-template-columns: 18px 1fr;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
}

.wc-step--done:not(.wc-step--open):hover {
  background: var(--hover-wash);
}

/* A done step's title reopens what it said: the whole row's text, no chrome. */
.wc-step__reopen {
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

/* The open step is a Card on the chrome: --surface, --radius-md, no shadow. */
.wc-step--open {
  background: rgb(var(--v-theme-surface));
  border-color: rgb(var(--v-theme-border));
}

/* The checked Checkbox look: olive fill, warm-white mark. A status, not a
   control, so it takes no focus. */
.wc-cb {
  width: 16px;
  height: 16px;
  margin-top: 2px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1.5px solid
    rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-primary));
}

.wc-cb--on {
  background: rgb(var(--v-theme-primary));
  border-color: rgb(var(--v-theme-primary));
}

.wc-step__title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
}

.wc-step--done .wc-step__title {
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wc-step__todo {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wc-step__said {
  margin: var(--space-2) 0 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
}

.wc-ctl {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.wc-ctl__label {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wc-link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--control-h-bar);
  padding: 0 var(--space-3);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
  text-decoration: none;
}

.wc-link:hover {
  background: var(--hover-wash);
}

.wc-foot {
  display: flex;
  justify-content: flex-end;
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
}
</style>
