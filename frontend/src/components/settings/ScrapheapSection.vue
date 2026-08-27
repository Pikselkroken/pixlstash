<script setup>
/**
 * Settings section for the scrapheap auto-empty (retention) policy.
 *
 * Owns no policy of its own: it reads and writes `useScrapheapRetentionStore`,
 * which is the single source of truth shared with the scrapheap view header.
 * Gated behind `isReadOnly === false` at the tab level in UserSettingsDialog.
 *
 * The retention window is a *server* setting (server-config.json), so changing
 * it affects every session - the copy says "PixlStash", not "you".
 */
import { computed, ref, watch } from "vue";
import { VIcon, VTooltip } from "vuetify/components";
import AppSelect from "../widgets/AppSelect.vue";
import SettingsSection from "./SettingsSection.vue";
import SettingsInfoCard from "./SettingsInfoCard.vue";
import RetentionReductionDialog from "../widgets/RetentionReductionDialog.vue";
import { useScrapheapRetentionStore } from "../../stores/useScrapheapRetentionStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { getScrapheapRetentionImpact } from "../../api/serverConfig";
import { formatUserDate } from "../../utils/utils";
import {
  buildRetentionReductionMessage,
  isRetentionReduction,
  retentionSelectOptions,
  retentionToSelectValue,
  selectValueToRetention,
} from "../../utils/retention";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const store = useScrapheapRetentionStore();
const userPrefsStore = useUserPrefsStore();

// Vuetify dialogs stay mounted after the first open, so onMounted would only
// ever fire once - fetch on the open transition instead (the house pattern).
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
// The select is NOT bound straight to the store: a reduction is only saved after
// a confirm, so between the pick and the answer the control shows a value the
// server has not accepted. A local ref lets a cancel push the old value back
// into the DOM - binding the store directly would leave the native <select>
// visually stuck on the abandoned choice, because the bound value never changed
// and Vue would have nothing to patch.
const selectValue = ref(retentionToSelectValue(store.retentionDays));
watch(
  () => store.retentionDays,
  (days) => {
    selectValue.value = retentionToSelectValue(days);
  },
);

/** Put the control back on the saved value after an abandoned change. */
function revertSelect() {
  selectValue.value = retentionToSelectValue(store.retentionDays);
}

const checkingImpact = ref(false);
const busy = computed(
  () => store.loading || store.saving || checkingImpact.value,
);

// ── Tooltip copy ────────────────────────────────────────────────────────────
// The things a user cannot infer from the control itself. Kept as data so
// the same strings feed the visible tooltip and the activator's accessible name.
// The grace period is read from the server rather than hardcoded, so the promise
// in the copy can never drift from what the purge task actually does.
const tooltipPoints = computed(() => {
  const grace = store.graceDays;
  const graceText = grace === 1 ? "one day of grace" : `${grace} days of grace`;
  return [
    "Auto-empty starts off. Nothing is deleted on a timer until you pick a window here.",
    "Applies to managed pictures only.",
    "Protected reference-folder originals are never auto-deleted.",
    `Turning it on, or shortening the window, gives everything already in the scrapheap ${graceText}, however old it is, so nothing is purged the instant you save.`,
  ];
});
const tooltipAriaLabel = computed(
  () => `What auto-emptying affects. ${tooltipPoints.value.join(" ")}`,
);

// ── State line ──────────────────────────────────────────────────────────────
// The control shows a WINDOW; on its own it does not say whether anything is
// running. Auto-empty now ships OFF, so "off" has to be a stated fact rather
// than the absence of one - and stating the ON case in the same place keeps the
// two symmetric, so neither reads as the default. Icon AND text, never colour
// alone. Server-wide setting, so the copy stays impersonal (see the header).
const stateLine = computed(() => {
  if (!store.loaded) return null;
  if (store.isNever) {
    return {
      icon: "mdi-timer-off-outline",
      text: "Auto-empty is off. Nothing is deleted from disk until the scrapheap is emptied by hand.",
    };
  }
  return {
    icon: "mdi-delete-clock-outline",
    text: `Auto-empty is on. Managed pictures are permanently deleted from disk ${store.label} after deletion.`,
  };
});

// ── Save ────────────────────────────────────────────────────────────────────
const savedFlash = ref(false);
let savedFlashToken = 0;

/** Persist the picked window and flash confirmation. */
async function commitRetention(days) {
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
    // which is rendered below. Put the control back on the saved value too, so
    // it can't sit showing a window the server rejected.
    console.warn("Scrapheap retention change was not saved.", err);
    revertSelect();
  }
}

// ── Reduction confirm ───────────────────────────────────────────────────────
// Lowering the window schedules permanent deletion; every other direction only
// spares pictures. So only a reduction is gated, and only when the server says
// it would actually delete something.
const reductionDialogOpen = ref(false);
const reductionMessage = ref({
  title: "",
  body: "",
  warning: "",
  confirmLabel: "Confirm",
});
const reductionUnverified = ref(false);
const pendingReductionDays = ref(null);

async function onSelect(value) {
  const days = selectValueToRetention(value);
  // Reflect the pick immediately; `revertSelect()` undoes it if we don't save.
  selectValue.value = value;

  const isReduction = isRetentionReduction(store.retentionDays, days, {
    // Never confirm against a baseline we haven't loaded - we'd be asserting a
    // direction we don't actually know.
    previousKnown: store.loaded,
  });
  if (!isReduction) {
    await commitRetention(days);
    return;
  }

  // Check the blast radius BEFORE saving. Mirrors the scrapheap delete-preview
  // fail-safe: never schedule destruction on an unverified basis.
  checkingImpact.value = true;
  let impact = null;
  let verified = true;
  try {
    impact = await getScrapheapRetentionImpact(days);
  } catch (err) {
    // Includes a 404 from a server that hasn't shipped the endpoint yet. Do NOT
    // guess a count and do NOT save silently - say so and let the user decide.
    console.warn(
      `Couldn't check the impact of lowering scrapheap retention to ${String(days)} days; ` +
        "asking the user to confirm on an unverified basis.",
      err,
    );
    verified = false;
  } finally {
    checkingImpact.value = false;
  }

  const message = buildRetentionReductionMessage({
    nextDays: days,
    // Passed RAW on purpose. Coercing a missing field to 0 here would make a
    // 200 with a malformed body look like "nothing would be deleted" and save
    // without asking; the builder routes an unreadable count to the unverified
    // confirm instead.
    wouldPurgeCount: impact?.would_purge_count,
    firstPurgeAt: impact?.first_purge_at ?? null,
    formatDate: (iso) => formatUserDate(iso, userPrefsStore.dateFormat),
    verified,
  });

  // A verified reduction that would delete nothing is not destructive: save it.
  if (!message) {
    await commitRetention(days);
    return;
  }

  reductionMessage.value = message;
  reductionUnverified.value = !verified;
  pendingReductionDays.value = days;
  reductionDialogOpen.value = true;
}

async function confirmReduction() {
  const days = pendingReductionDays.value;
  reductionDialogOpen.value = false;
  pendingReductionDays.value = null;
  await commitRetention(days);
}

function cancelReduction() {
  pendingReductionDays.value = null;
  // Nothing was saved, so the control must not keep showing the abandoned pick.
  revertSelect();
}
</script>

<template>
  <div>
    <SettingsSection
      title="Scrapheap"
      desc="Pictures you delete land in the scrapheap first. If you want, PixlStash can empty it for you after a set time."
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

      <p v-if="stateLine" class="sr-state" role="status">
        <v-icon size="14" class="sr-state__icon">{{ stateLine.icon }}</v-icon>
        <span>{{ stateLine.text }}</span>
      </p>

      <div v-if="store.error" class="sr-error" role="alert">
        {{ store.error }}
      </div>
      <div v-else-if="savedFlash" class="sr-success" role="status">Saved.</div>

      <RetentionReductionDialog
        v-model:open="reductionDialogOpen"
        :title="reductionMessage.title"
        :body="reductionMessage.body"
        :warning="reductionMessage.warning"
        :confirm-label="reductionMessage.confirmLabel"
        :unverified="reductionUnverified"
        :busy="store.saving"
        @confirm="confirmReduction"
        @cancel="cancelReduction"
      />

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
  color: rgba(var(--v-theme-on-surface), 0.6);
  border-radius: var(--radius-sm);
  transition: color var(--dur-1) var(--ease-standard);
}

.sr-info:hover {
  color: rgb(var(--v-theme-on-surface));
  background: var(--hover-wash);
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

/* States the policy in words, so "off" is something the user reads rather than
   infers from a dropdown that happens to say Never. */
.sr-state {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.sr-state__icon {
  flex-shrink: 0;
  /* Optical: lifts the glyph onto the first line's cap height. */
  margin-top: var(--space-1);
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
