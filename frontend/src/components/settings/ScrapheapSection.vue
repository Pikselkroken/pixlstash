<script setup>
/**
 * Settings section for the scrapheap auto-empty (retention) policy.
 *
 * Owns no policy of its own: it reads and writes `useScrapheapRetentionStore`,
 * which is the single source of truth shared with the scrapheap view header.
 * Gated behind `isReadOnly === false` at the tab level in UserSettingsDialog.
 *
 * The retention window is a *server* setting (server-config.json), so changing
 * it affects every session — the copy says "PixlStash", not "you".
 */
import { computed, ref, watch } from "vue";
import { VIcon, VTooltip } from "vuetify/components";
import AppSelect from "../widgets/AppSelect.vue";
import SettingsSection from "./SettingsSection.vue";
import SettingsInfoCard from "./SettingsInfoCard.vue";
import { useScrapheapRetentionStore } from "../../stores/useScrapheapRetentionStore";
import {
  retentionSelectOptions,
  retentionToSelectValue,
  selectValueToRetention,
} from "../../utils/retention";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const store = useScrapheapRetentionStore();

// Vuetify dialogs stay mounted after the first open, so onMounted would only
// ever fire once — fetch on the open transition instead (the house pattern).
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) store.fetchRetention();
  },
  { immediate: true },
);

// The server declares which windows it accepts, so the select can never offer a
// value the PATCH would reject with a 422.
const options = computed(() =>
  retentionSelectOptions(store.retentionDays, store.choices),
);
const selectValue = computed(() => retentionToSelectValue(store.retentionDays));
const busy = computed(() => store.loading || store.saving);

// ── Tooltip copy ────────────────────────────────────────────────────────────
// The three things a user cannot infer from the control itself. Kept as data so
// the same strings feed the visible tooltip and the activator's accessible name.
// The grace period is read from the server rather than hardcoded, so the promise
// in the copy can never drift from what the purge task actually does.
const tooltipPoints = computed(() => {
  const grace = store.graceDays;
  const graceText = grace === 1 ? "one day of grace" : `${grace} days of grace`;
  return [
    "Applies to managed pictures only.",
    "Protected reference-folder originals are never auto-deleted.",
    `Shortening the window gives everything already in the scrapheap ${graceText}, however old it is, so nothing is purged the instant you save.`,
  ];
});
const tooltipAriaLabel = computed(
  () => `What auto-emptying affects. ${tooltipPoints.value.join(" ")}`,
);

// ── Save ────────────────────────────────────────────────────────────────────
const savedFlash = ref(false);
let savedFlashToken = 0;

async function onSelect(value) {
  const days = selectValueToRetention(value);
  savedFlash.value = false;
  try {
    await store.setRetention(days);
    const token = ++savedFlashToken;
    savedFlash.value = true;
    setTimeout(() => {
      if (savedFlashToken === token) savedFlash.value = false;
    }, 2000);
  } catch (err) {
    // The store already rolled the optimistic value back and set `store.error`,
    // which is rendered below; nothing further to do here.
    console.warn("Scrapheap retention change was not saved.", err);
  }
}
</script>

<template>
  <div>
    <SettingsSection
      title="Scrapheap"
      desc="Pictures you delete land in the scrapheap first. PixlStash can empty it for you after a set time."
      first
    >
      <div class="sr-row">
        <AppSelect
          class="sr-select"
          label="Auto-empty scrapheap after"
          :options="options"
          :model-value="selectValue"
          :disabled="busy"
          @update:model-value="onSelect"
        />
        <v-tooltip location="bottom" max-width="320" open-on-focus>
          <template #activator="{ props: tooltipProps }">
            <button
              v-bind="tooltipProps"
              type="button"
              class="sr-info"
              :aria-label="tooltipAriaLabel"
            >
              <v-icon size="16">mdi-information-outline</v-icon>
            </button>
          </template>
          <ul class="sr-tip">
            <li v-for="point in tooltipPoints" :key="point">{{ point }}</li>
          </ul>
        </v-tooltip>
      </div>

      <p v-if="store.isNever" class="sr-clarifier">
        Nothing is auto-removed; empty the scrapheap manually.
      </p>

      <div v-if="store.error" class="sr-error" role="alert">
        {{ store.error }}
      </div>
      <div v-else-if="savedFlash" class="sr-success" role="status">Saved.</div>

      <div class="sr-note">
        <SettingsInfoCard>
          Reference-folder originals in the scrapheap are protected: they are
          never auto-deleted, and their tiles say so.
        </SettingsInfoCard>
      </div>
    </SettingsSection>
  </div>
</template>

<style scoped>
/* Select + its info affordance on one baseline; the button aligns to the field,
   not to the uppercase field label above it. */
.sr-row {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
}

.sr-select {
  max-width: 220px;
  flex: 0 1 220px;
}

.sr-info {
  /* Matches the AppSelect field height so the two share a bottom edge. */
  height: 27px;
  width: 27px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.6);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: color var(--dur-1) var(--ease-standard);
}

.sr-info:hover {
  color: rgb(var(--v-theme-on-surface));
  background: var(--hover-wash);
}

.sr-info:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.sr-tip {
  margin: 0;
  padding-left: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.sr-clarifier {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.sr-note {
  margin-top: var(--space-5);
}

.sr-error {
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
}

.sr-success {
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-success));
}
</style>
