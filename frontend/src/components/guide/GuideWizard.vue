<script setup>
// A Guide task as a wizard on the real screen (#2825): a scrim with a hole cut
// over the control, its steps data (guide/catalogue.js) rather than code.
//
// Each step can first go somewhere (a route, or a Settings pane), then rings
// its `target` once it is on screen and waits for the user to click,
// right-click or double-click inside it. The card never offers Next: doing the
// ringed thing is how a step moves on. While a target is not on screen (a
// menu not open yet, a control this library does not show) there is no scrim
// at all, only the card, so nothing is ever blocked; Skip step moves on.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon } from "vuetify/components";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import { useGuideStore } from "../../stores/useGuideStore";

const props = defineProps({
  /** Opens a Settings pane by its tab id (App.vue owns the dialog). */
  openSettings: { type: Function, required: true },
});

const guide = useGuideStore();
const route = useRoute();
const router = useRouter();

const task = computed(() => guide.wizardTask);
const concept = computed(() => guide.wizardConcept);
const index = computed(() => guide.wizard?.step ?? 0);
const step = computed(() => task.value?.steps[index.value] ?? null);
const count = computed(() => task.value?.steps.length ?? 0);
const isLast = computed(() => index.value + 1 >= count.value);

// ── Going there ──
async function arrive(s) {
  if (!s) return;
  const go = s.go;
  if (typeof go === "string" && go.startsWith("settings:")) {
    if (route.path.startsWith("/guide")) await router.push("/");
    props.openSettings(go.slice("settings:".length));
    return;
  }
  if (typeof go === "string" && go && route.path !== go) {
    await router.push(go);
    return;
  }
  // A step that names nowhere to go still has to leave the Guide: its
  // control is on the real screen, never on the Guide's own.
  if (route.path.startsWith("/guide")) await router.push("/");
}
watch(step, (s) => arrive(s), { immediate: true });

// ── The ring: measured every frame, since menus and dialogs animate in. ──
// ponytail: an rAF poll while a wizard runs; a MutationObserver if it ever
// shows in a profile.
const rect = ref(null);
const viewport = ref({ w: 0, h: 0 });
let targetEl = null;
let frame = 0;
const PAD = 4;

function visibleMatch(selector) {
  let nodes;
  try {
    nodes = document.querySelectorAll(selector);
  } catch (e) {
    console.error("Guide: a step's target is not a valid selector:", selector, e);
    return null;
  }
  for (const el of nodes) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && !el.closest(".gwz")) return el;
  }
  return null;
}

function measure() {
  const found = step.value?.target ? visibleMatch(step.value.target) : null;
  // The scrim takes the wheel, so a target below the fold could never be
  // scrolled to: bring it into view once, as it appears.
  if (found && found !== targetEl)
    found.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  targetEl = found;
  const r = targetEl?.getBoundingClientRect();
  rect.value = r
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
  if (!r) return "";
  return `path(evenodd, "M0 0H${w}V${h}H0Z M${r.x} ${r.y}h${r.w}v${r.h}h${-r.w}Z")`;
});

// The card sits bottom centre unless the target is down there.
// ponytail: a fixed 240px band; measure the card if its text ever grows.
const CARD_BAND_PX = 240;
const cardAtTop = computed(
  () => !!rect.value && rect.value.y + rect.value.h > viewport.value.h - CARD_BAND_PX,
);

// ── Doing the ringed thing moves the wizard on ──
// After the app has had the event: the next step's target (the menu the
// right-click opens) only exists once it has.
function onAct(event) {
  if (!targetEl || !(event.target instanceof Node)) return;
  if (!targetEl.contains(event.target)) return;
  const at = index.value;
  setTimeout(() => {
    if (guide.wizard && guide.wizard.step === at) guide.nextStep();
  }, 0);
}

// Esc ends the wizard and nothing else: a menu or dialog it rings stays open.
function onKeydown(event) {
  if (event.key !== "Escape") return;
  event.stopPropagation();
  guide.endWizard();
}

// ── Or the next step's control appearing moves it on ──
// A step with nothing to ring ("Right-click the Sets heading") has no other
// way to notice it was done, and one with a ring can be done another way (a
// shortcut). "Appearing" means absent at some point during this step, so a
// control already on screen never skips one; and only after the user acted on
// the app, so a route the step itself opened does not either.
let followFrame = 0;
// The store replaces `wizard` on every step and every task: one object, one step.
let followedWizard = null;
let nextWasAbsent = false;
let userActed = false;

function nextTargetShown(selector) {
  try {
    return [...document.querySelectorAll(selector)].some((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && !el.closest(".gwz");
    });
  } catch (e) {
    console.error("Guide: a step's target is not a valid selector:", selector, e);
    return false;
  }
}

function followNextTarget() {
  const at = index.value;
  if (followedWizard !== guide.wizard) {
    followedWizard = guide.wizard;
    nextWasAbsent = false;
    userActed = false;
  }
  const next = task.value?.steps[at + 1]?.target;
  if (next) {
    if (!nextTargetShown(next)) {
      // Only an act from here on can be what brings it up.
      if (!nextWasAbsent) userActed = false;
      nextWasAbsent = true;
    } else if (nextWasAbsent && userActed) guide.nextStep();
  }
  followFrame = requestAnimationFrame(followNextTarget);
}

/** Anything done on the app counts; the card's own Back and Skip do not. */
function noteUserActed(event) {
  const t = event.target;
  const el = t instanceof Element ? t : t?.parentElement;
  if (!el?.closest(".gwz")) userActed = true;
}

const ACT_EVENTS = ["click", "contextmenu", "dblclick", "keydown"];
onMounted(() => {
  for (const type of ACT_EVENTS) window.addEventListener(type, noteUserActed, true);
  followFrame = requestAnimationFrame(followNextTarget);
});
onBeforeUnmount(() => {
  for (const type of ACT_EVENTS) window.removeEventListener(type, noteUserActed, true);
  cancelAnimationFrame(followFrame);
});

const cardRef = ref(null);
watch(index, () => nextTick(() => cardRef.value?.focus?.()));

onMounted(() => {
  for (const type of ["click", "contextmenu", "dblclick"])
    window.addEventListener(type, onAct, true);
  window.addEventListener("keydown", onKeydown, true);
  frame = requestAnimationFrame(measure);
  nextTick(() => cardRef.value?.focus?.());
});
onBeforeUnmount(() => {
  for (const type of ["click", "contextmenu", "dblclick"])
    window.removeEventListener(type, onAct, true);
  window.removeEventListener("keydown", onKeydown, true);
  cancelAnimationFrame(frame);
  // Focus would drop to body with the card: onto the last ringed control
  // when it is still there, which is where the user's work is.
  const last = targetEl;
  nextTick(() => {
    if (last?.isConnected) last.focus?.();
  });
});
</script>

<template>
  <div v-if="step" class="gwz" data-testid="guide-wizard">
    <div v-if="rect" class="gwz-scrim" :style="{ clipPath: scrimClip }"></div>
    <div
      v-if="rect"
      class="gwz-ring"
      :style="{
        left: `${rect.x}px`,
        top: `${rect.y}px`,
        width: `${rect.w}px`,
        height: `${rect.h}px`,
      }"
    ></div>
    <section
      ref="cardRef"
      class="gwz-card"
      :class="{ 'gwz-card--top': cardAtTop }"
      role="dialog"
      :aria-label="`${task.name}: step ${index + 1} of ${count}`"
      tabindex="-1"
    >
      <div class="gwz-kicker">
        <span>{{ concept.name }} · step {{ index + 1 }} of {{ count }}</span>
        <span class="gwz-sp"></span>
        <AppBarButton
          icon="close"
          :icon-size="18"
          tooltip="End (Esc)"
          data-testid="guide-wizard-end"
          @click="guide.endWizard()"
        />
      </div>
      <div aria-live="polite">
        <h4 class="gwz-title">{{ task.name }}</h4>
        <p v-if="index === 0" class="gwz-text">{{ task.sentence }}</p>
        <p class="gwz-do" data-testid="guide-wizard-say">
          <v-icon size="18" aria-hidden="true">mdi-cursor-default-click-outline</v-icon>
          {{ step.say }}
        </p>
      </div>
      <div class="gwz-acts">
        <AppButton
          v-if="index > 0"
          variant="ghost"
          icon-left="arrow-left"
          @click="guide.previousStep()"
          >Back</AppButton
        >
        <span class="gwz-sp"></span>
        <AppButton
          variant="ghost"
          data-testid="guide-wizard-skip"
          @click="guide.nextStep()"
          >{{ isLast ? "Done" : "Skip step" }}</AppButton
        >
      </div>
    </section>
  </div>
</template>

<style scoped>
/* One layer for the whole wizard, where the LoRA tour sat: above the app's
   dialogs, under the notice surface. Inside it the scrim, ring and card
   stack in DOM order. */
.gwz {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  pointer-events: none;
}

.gwz-scrim {
  position: fixed;
  inset: 0;
  pointer-events: auto;
  background: rgba(var(--v-theme-scrim), 0.4);
}

/* Amber, because it means "click here". The ring takes no clicks: the
   target under it does. */
.gwz-ring {
  position: fixed;
  pointer-events: none;
  border-radius: var(--radius-sm);
  box-shadow:
    0 0 0 2px rgb(var(--v-theme-accent)),
    0 0 0 6px rgba(var(--v-theme-accent), 0.3);
}

.gwz-card {
  position: fixed;
  left: 50%;
  bottom: var(--space-5);
  transform: translateX(-50%);
  pointer-events: auto;
  width: min(420px, calc(100vw - 2 * var(--space-5)));
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4) var(--space-4) var(--space-5);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-4);
}

.gwz-card:focus {
  outline: none;
}

.gwz-card--top {
  top: calc(var(--titlebar-h) + var(--space-5));
  bottom: auto;
}

.gwz-kicker {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.gwz-sp {
  flex: 1;
}

.gwz-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.gwz-text {
  margin: var(--space-1) 0 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.gwz-do {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: var(--space-3) 0 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.gwz-do .v-icon {
  color: rgb(var(--v-theme-accent));
}

.gwz-acts {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
</style>
