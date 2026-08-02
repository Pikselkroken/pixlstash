<template>
  <v-dialog :model-value="open" max-width="736" @update:model-value="onDismiss">
    <v-card class="tc">
      <h2 class="tc__title">
        {{ isUpgrade ? "One new thing in 1.9" : "What may PixlStash send?" }}
      </h2>

      <p class="tc__lede">
        <template v-if="isUpgrade">
          You already have update checks turned on. You could help PixlStash
          improve by sending a random number alongside them. Nothing else about
          your setup changes either way.
        </template>
        <template v-else>
          PixlStash can check pixlstash.dev once a day for a new version.
          Several past releases fixed critical security bugs, so I'd suggest
          leaving this on. You could also help PixlStash improve by sending a
          random number alongside it.
        </template>
      </p>

      <!-- radiogroup, not a set of buttons: the three are mutually exclusive
           answers to one question, so arrow keys move between them and the
           group is a single tab stop. -->
      <div
        ref="groupEl"
        class="tc__options"
        :class="{ 'tc__options--two': options.length === 2 }"
        role="radiogroup"
        :aria-label="
          isUpgrade ? 'Whether to add a random ID' : 'What PixlStash may send'
        "
      >
        <button
          v-for="(opt, i) in options"
          :key="opt.key"
          type="button"
          role="radio"
          class="tc__opt"
          :aria-checked="selected === i"
          :tabindex="selected === i || (selected === null && i === 0) ? 0 : -1"
          @click="select(i)"
          @mouseenter="preview = opt.key"
          @mouseleave="preview = null"
          @focus="preview = opt.key"
          @blur="preview = null"
          @keydown="onKeydown($event, i)"
        >
          <TelemetryOptionMark class="tc__mark" :variant="opt.key" />
          <span class="tc__opt-name">{{ opt.name }}</span>
          <p class="tc__opt-desc">{{ opt.desc }}</p>
        </button>
      </div>

      <TelemetryPayloadPreview
        :variant="shownVariant"
        :version="version"
        :install-type="installType"
      />

      <p class="tc__never">
        <strong>Never sent:</strong>
        your images &middot; your tags, captions or filenames &middot; your
        search queries &middot; your file paths
      </p>

      <p class="tc__foot">
        You can change this any time in Settings, and regenerate the random ID
        whenever you like.
      </p>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { VCard, VDialog } from "vuetify/components";
import TelemetryOptionMark from "../widgets/TelemetryOptionMark.vue";
import TelemetryPayloadPreview from "../widgets/TelemetryPayloadPreview.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** True for an existing user upgrading, who already answered the update question. */
  isUpgrade: { type: Boolean, default: false },
  version: { type: String, default: "" },
  installType: { type: String, default: "" },
});

const emit = defineEmits(["decide"]);

const groupEl = ref(null);
const selected = ref(null);
const preview = ref(null);

const NEW_OPTIONS = [
  {
    key: "none",
    name: "No check",
    desc: "Nothing leaves your machine. You'll need to watch for security releases yourself.",
    patch: { check_for_updates: false, telemetry_send_install_id: false },
  },
  {
    key: "check",
    name: "Check for updates",
    desc: "Sends your version and platform. Nothing else.",
    patch: { check_for_updates: true, telemetry_send_install_id: false },
  },
  {
    key: "checkid",
    name: "Check + random ID",
    desc: "Adds a random number, so I can tell ten people using PixlStash once from one person using it ten times.",
    patch: { check_for_updates: true, telemetry_send_install_id: true },
  },
];

// The upgrade variant deliberately omits check_for_updates from both patches.
// Re-asking a decision the user already made would let "No thanks" silently
// switch off update checks they had turned on, which is a regression dressed
// as a consent flow.
const UPGRADE_OPTIONS = [
  {
    key: "check",
    name: "No thanks",
    desc: "Your update checks carry on exactly as they are.",
    patch: { telemetry_send_install_id: false },
  },
  {
    key: "checkid",
    name: "Add the random number",
    desc: "So I can tell ten people using PixlStash once from one person using it ten times.",
    patch: { telemetry_send_install_id: true },
  },
];

const options = computed(() =>
  props.isUpgrade ? UPGRADE_OPTIONS : NEW_OPTIONS,
);

/** Hover and focus preview transiently; the committed choice is the fallback. */
const shownVariant = computed(
  () =>
    preview.value ??
    (selected.value === null ? null : options.value[selected.value].key),
);

function decide(patch) {
  // Every exit path records that the question was asked, so it is never raised
  // again regardless of how the user left.
  emit("decide", { ...patch, telemetry_consent_prompted: true });
}

function select(index) {
  selected.value = index;
  decide(options.value[index].patch);
}

/**
 * Dismissal is a valid answer, not an escape hatch.
 *
 * Everything stays off and the prompt is recorded as asked, so a user who
 * closes the dialog gets exactly what a user who picks the decline option
 * gets. Trapping them until they answer a request for help would not be
 * freely-given consent.
 */
function onDismiss(value) {
  if (value) return;
  const declined = options.value[0].patch;
  decide(declined);
}

function onKeydown(event, index) {
  const items = groupEl.value?.querySelectorAll(".tc__opt") ?? [];
  let next = null;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") {
    next = (index + 1) % items.length;
  }
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
    next = (index + items.length - 1) % items.length;
  }
  if (next !== null) {
    event.preventDefault();
    selected.value = null; // move focus without committing
    items[next].focus();
    return;
  }
  if (event.key === " " || event.key === "Enter") {
    event.preventDefault();
    select(index);
  }
}

// Focus the first option rather than a button: the user should read before
// acting, and there is no primary action to land on.
watch(
  () => props.open,
  async (isOpen) => {
    if (!isOpen) return;
    selected.value = null;
    preview.value = null;
    await nextTick();
    groupEl.value?.querySelector(".tc__opt")?.focus();
  },
);
</script>

<style scoped>
.tc {
  padding: var(--space-7);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.tc__title {
  font-size: var(--text-lg);
  line-height: var(--leading-tight);
  font-weight: var(--weight-semibold);
  margin: 0;
}

.tc__lede {
  margin: 0;
  font-size: var(--text-base);
  line-height: var(--leading-body);
}

.tc__options {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-4);
}

.tc__options--two {
  grid-template-columns: repeat(2, 1fr);
}

/* Three side-by-side boxes need roughly 46rem; below that they stack. */
@media (max-width: 44rem) {
  .tc__options,
  .tc__options--two {
    grid-template-columns: 1fr;
  }
}

/* Every option carries identical border, background and type treatment. No
   option is emphasised: the lede does the recommending, and a visually
   privileged accept path is what turns consent into a dark pattern. */
.tc__opt {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  text-align: left;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  padding: var(--space-5) var(--space-4);
  cursor: pointer;
  font: inherit;
  transition:
    border-color var(--dur-1) var(--ease-standard),
    background-color var(--dur-1) var(--ease-standard);
}

.tc__opt:hover {
  border-color: rgb(var(--v-theme-accent));
  background: rgba(var(--v-theme-accent), 0.06);
}

.tc__opt:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.tc__opt[aria-checked="true"] {
  border-color: rgb(var(--v-theme-accent));
  background: rgba(var(--v-theme-accent), 0.1);
}

.tc__mark {
  color: rgb(var(--v-theme-accent));
}

.tc__opt-name {
  font-weight: var(--weight-semibold);
  font-size: var(--text-md);
  line-height: var(--leading-snug);
}

.tc__opt-desc {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.tc__never {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.tc__never strong {
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-medium);
}

.tc__foot {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

@media (prefers-reduced-motion: reduce) {
  .tc__opt {
    transition-duration: 0.01ms;
  }
}
</style>
