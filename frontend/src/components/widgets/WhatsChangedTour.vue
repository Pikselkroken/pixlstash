<script setup>
// The 2.0 training tour (#2145): four steps from "pick who to train" to
// Models › Training runs. Next does each click for you; clicking the ringed
// thing yourself does the same and moves the tour on. Start in the Train
// dialog is the one click it never makes: Next on that step closes the dialog,
// and Enter inside the dialog is taken as Next rather than reaching the
// dialog's Enter-accepts (which would start a run).
//
// The window sits under the scrim with a hole cut over the target, so only
// the target and this panel take clicks. App.vue supplies the app moves, and
// mounts this only while the tour runs.
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { VIcon } from "vuetify/components";
import AppButton from "./AppButton.vue";
import { useWhatsChangedStore } from "../../stores/useWhatsChangedStore";

const props = defineProps({
  /**
   * The app moves, each given the tour's subject `{ type, id, name, count }`:
   * `select`, `openMenu`, `closeMenu`, `openTrain`, `closeTrain`, `goModels`,
   * and the readings `isSelected`, `trainOpen` and `onModels`.
   */
  actions: { type: Object, required: true },
});

const whatsChanged = useWhatsChangedStore();
const step = computed(() => whatsChanged.tourStep);
const subject = computed(() => whatsChanged.tourSubject);
const STEP_COUNT = 4;

const STEPS = {
  1: {
    target: (s) => `[data-tour-row="${s.type}:${s.id}"]`,
    title: () => "Pick who to train.",
    text: (s) =>
      s.type === "character"
        ? `A LoRA learns one person or one set. We picked ${s.name}, who has the most pictures.`
        : `A LoRA learns one person or one set. We picked ${s.name}, the largest set.`,
  },
  2: {
    target: () => '[data-tour="train-lora"]',
    title: (s) =>
      s.type === "character"
        ? "Right-click the person, then Train LoRA…"
        : "Right-click the set, then Train LoRA…",
    text: (s) =>
      `That opens the Train dialog for ${
        s.type === "character" ? `${s.name}'s` : `the ${s.name} set's`
      } ${s.count.toLocaleString()} pictures. Click it yourself, or press Next and the tour does it for you.`,
  },
  3: {
    target: () => '.app-dialog__footer:has([data-testid="tld-start"])',
    title: () => "The Train dialog does the sums.",
    text: () =>
      "It asks what the pictures are of and how hard to train, then says how long that takes on this machine. Start is yours; the tour will not press it.",
  },
  4: {
    target: () => '[data-workspace="models"]',
    title: () => "Watch it in Models › Training runs.",
    text: () =>
      "Every checkpoint gets preview pictures under its point on the loss curve, so you can pick the step that looks right.",
  },
};

const current = computed(() =>
  step.value && subject.value ? STEPS[step.value] : null,
);

// ── The ring: measured every frame, since menus and dialogs animate in. ──
// ponytail: an rAF poll while the tour runs; a ResizeObserver per target if it
// ever shows up in a profile.
const rect = ref(null);
const viewport = ref({ w: 0, h: 0 });
let frame = 0;
const PAD = 4;

function measure() {
  const el = current.value
    ? document.querySelector(current.value.target(subject.value))
    : null;
  const r = el?.getBoundingClientRect?.();
  rect.value =
    r && r.width > 0 && r.height > 0
      ? {
          x: r.left - PAD,
          y: r.top - PAD,
          w: r.width + PAD * 2,
          h: r.height + PAD * 2,
        }
      : null;
  viewport.value = { w: window.innerWidth, h: window.innerHeight };
  frame = requestAnimationFrame(measure);
}

/** The scrim, with the target cut out of it: drawn AND hit-tested. */
const scrimClip = computed(() => {
  const { w, h } = viewport.value;
  const r = rect.value;
  const outer = `M0 0H${w}V${h}H0Z`;
  if (!r) return `path(evenodd, "${outer}")`;
  return `path(evenodd, "${outer} M${r.x} ${r.y}h${r.w}v${r.h}h${-r.w}Z")`;
});

// The panel sits bottom centre unless the target is down there (the Train
// dialog's Start row), where it would cover the very thing it points at.
// ponytail: a fixed 220px band, measure the panel if its text ever grows.
const COACH_BAND_PX = 220;
const coachAtTop = computed(
  () => !!rect.value && rect.value.y + rect.value.h > viewport.value.h - COACH_BAND_PX,
);

// ── Moving between steps ──
// While the tour itself is moving the app, the watchers below must not read
// that move as the user's own click. Likewise a press on the panel: the Train
// dialog closes on a click outside it, before the panel's button runs.
let busy = false;
let panelPress = false;

async function go(to, move) {
  busy = true;
  try {
    await move?.();
    await nextTick();
  } finally {
    busy = false;
  }
  if (to > STEP_COUNT) {
    whatsChanged.finishTour();
    return;
  }
  whatsChanged.tourStep = to;
}

const a = props.actions;
function next() {
  const s = subject.value;
  if (!s) return end();
  if (step.value === 1)
    return go(2, async () => {
      await a.select(s);
      await nextTick();
      a.openMenu(s);
    });
  if (step.value === 2) return go(3, () => a.openTrain(s));
  if (step.value === 3) return go(4, () => a.closeTrain());
  return go(STEP_COUNT + 1, () => a.goModels());
}

function previous() {
  const s = subject.value;
  if (!s || step.value <= 1) return;
  if (step.value === 2) return go(1, () => a.closeMenu());
  if (step.value === 3)
    return go(2, async () => {
      a.closeTrain();
      await nextTick();
      a.openMenu(s);
    });
  return go(3, () => a.openTrain(s));
}

/**
 * End tour, Esc: stop where you are, and the card stays to start again. The
 * one exception is the Train dialog the tour opened: it goes too, since the
 * Enter guard goes with the tour and Enter there would start a run.
 */
function end() {
  if (step.value === 3 && a.trainOpen()) a.closeTrain();
  whatsChanged.tourStep = 0;
}

// Doing the ringed thing yourself moves the tour on.
const userMove = (fn) => (value, old) => {
  if (busy || panelPress || value === old) return;
  fn(value);
};
watch(
  () => (subject.value ? a.isSelected(subject.value) : false),
  userMove((selected) => {
    if (step.value === 1 && selected) go(2, () => a.openMenu(subject.value));
  }),
);
watch(
  () => a.trainOpen(),
  userMove((open) => {
    if (step.value === 2 && open) go(3);
    else if (step.value === 3 && !open) go(4);
  }),
);
watch(
  () => a.onModels(),
  userMove((on) => {
    if (step.value === 4 && on) go(STEP_COUNT + 1);
  }),
);

// The subject can vanish mid-tour (deleted, library switched).
watch(subject, (s) => {
  if (!s && step.value) end();
});

// ── Focus and keys ──
const nextRef = ref(null);
watch(
  step,
  (n) => {
    if (n) nextTick(() => nextRef.value?.$el?.focus?.());
  },
  { immediate: true },
);

function onKeydown(event) {
  if (!step.value) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopImmediatePropagation();
    end();
    return;
  }
  // Inside the Train dialog Enter would accept it, which starts a run.
  if (
    step.value === 3 &&
    event.key === "Enter" &&
    event.target instanceof Element &&
    !event.target.closest(".wct-coach")
  ) {
    event.preventDefault();
    event.stopImmediatePropagation();
    next();
  }
}

function onPanelPointerDown() {
  panelPress = true;
  // A touch that turns into a scroll ends in pointercancel, not pointerup.
  const release = () => setTimeout(() => (panelPress = false), 0);
  window.addEventListener("pointerup", release, { once: true });
  window.addEventListener("pointercancel", release, { once: true });
}

onMounted(() => {
  window.addEventListener("keydown", onKeydown, true);
  frame = requestAnimationFrame(measure);
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown, true);
  cancelAnimationFrame(frame);
  // Every way out unmounts the focused panel: back to Show me, which started
  // it, once it has re-rendered enabled. After the last step the card is gone
  // too, so onto the grid's rail when there is one.
  nextTick(() =>
    (
      document.querySelector('[data-testid="headline-show-me"]') ??
      document.querySelector(".stats-sidebar .inspector-tab--active, .rail-clear")
    )?.focus?.(),
  );
});
</script>

<template>
  <div v-if="current" class="wct" data-testid="whats-changed-tour">
    <div class="wct-scrim" :style="{ clipPath: scrimClip }"></div>
    <div
      v-if="rect"
      class="wct-ring"
      :style="{
        left: `${rect.x}px`,
        top: `${rect.y}px`,
        width: `${rect.w}px`,
        height: `${rect.h}px`,
      }"
    ></div>
    <section
      class="wct-coach"
      :class="{ 'wct-coach--top': coachAtTop }"
      role="dialog"
      aria-label="LoRA training tour"
      @pointerdown.capture="onPanelPointerDown"
    >
      <div class="wct-n">
        <span>Step {{ step }} of {{ STEP_COUNT }}</span>
        <span class="wct-dots" aria-hidden="true"
          ><i v-for="n in STEP_COUNT" :key="n" :class="{ on: n <= step }"></i
        ></span>
      </div>
      <div aria-live="polite">
        <h4 class="wct-title">{{ current.title(subject) }}</h4>
        <p class="wct-text">{{ current.text(subject) }}</p>
      </div>
      <div class="wct-acts">
        <AppButton variant="ghost" key-hint="esc" @click="end"
          >End tour</AppButton
        >
        <span class="wct-sp"></span>
        <AppButton
          v-if="step > 1"
          variant="ghost"
          icon-left="arrow-left"
          @click="previous"
          >Previous</AppButton
        >
        <AppButton
          ref="nextRef"
          variant="primary"
          data-testid="tour-next"
          @click="next"
          >{{ step === STEP_COUNT ? "Done" : "Next" }}
          <v-icon v-if="step < STEP_COUNT" size="16"
            >mdi-arrow-right</v-icon
          ></AppButton
        >
      </div>
    </section>
  </div>
</template>

<style scoped>
/* One layer for the whole tour: above the Train dialog (Vuetify overlays
   sit under --z-modal) and under the notice surface, so the tour's own toasts
   stay readable. Inside it the scrim, ring and panel stack in DOM order. */
.wct {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  pointer-events: none;
}

.wct-scrim {
  position: fixed;
  inset: 0;
  pointer-events: auto;
  background: rgba(var(--v-theme-scrim), 0.8);
}

/* Amber, because it means "click here". The ring takes no clicks: the
   target under it does. */
.wct-ring {
  position: fixed;
  pointer-events: none;
  border-radius: var(--radius-sm);
  box-shadow:
    0 0 0 2px rgb(var(--v-theme-accent)),
    0 0 0 6px rgba(var(--v-theme-accent), 0.3);
}

/* The selection pill's bottom-centre spot, empty while the tour runs. */
.wct-coach {
  position: fixed;
  left: 50%;
  bottom: var(--space-5);
  transform: translateX(-50%);
  pointer-events: auto;
  width: min(680px, calc(100vw - 2 * var(--space-5)));
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-2) var(--space-4);
  align-items: start;
  padding: var(--space-4) var(--space-5);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-4);
}

.wct-coach--top {
  top: var(--space-5);
  bottom: auto;
}

.wct-n {
  grid-row: span 2;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: 3px;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wct-dots {
  display: flex;
  gap: var(--space-2);
}

.wct-dots i {
  width: 14px;
  height: 4px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.18);
}

.wct-dots i.on {
  background: rgb(var(--v-theme-tertiary));
}

.wct-title {
  margin: 0;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.wct-text {
  margin: var(--space-1) 0 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wct-acts {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.wct-sp {
  flex: 1;
}
</style>
