<template>
  <div class="tp" aria-live="polite">
    <span class="tp__label">{{ label }}</span>
    <pre class="tp__body">{{ rendered }}</pre>
    <dl v-if="legend.length" class="tp__legend">
      <template v-for="entry in legend" :key="entry.term">
        <dt>{{ entry.term }}</dt>
        <dd>{{ entry.meaning }}</dd>
      </template>
    </dl>
  </div>
</template>

<script setup>
import { computed } from "vue";
import {
  buildPayloadForChoice,
  buildPayloadLegend,
} from "../../utils/telemetryPayload";

const props = defineProps({
  /** "none", "check", "id", "checkid", or null before hover/focus. */
  variant: { type: String, default: null },
  version: { type: String, default: "" },
  installType: { type: String, default: "" },
  /** Actual local classification; the UUID remains a placeholder. */
  isNewInstall: { type: Boolean, default: false },
});

// A placeholder ID, not this machine's. The shape is what matters; unlike the
// identifier, is_new_install is the real local value because showing `true` to
// every upgrading user would make the consent preview disagree with the sender.
const SAMPLE_ID = "9f2c1b7e-4d5a-4c81-b3e6-8a7d2f0e5c14";

const context = computed(() => ({
  version: props.version || "1.9.0",
  installType: props.installType || "pip",
  installId: SAMPLE_ID,
  isNewInstall: props.isNewInstall,
}));

const label = computed(() =>
  props.variant && props.variant !== "none"
    ? "What this sends, once a day"
    : "What this sends",
);

const rendered = computed(() => {
  if (!props.variant) return "Hover or focus an option above.";
  const requests = buildPayloadForChoice(props.variant, context.value);
  if (!requests.length)
    return "No request is made.\nPixlStash contacts nothing.";
  return requests
    .map((req) => {
      const head = `${req.method} ${req.url}`;
      return req.body ? `${head}\n${JSON.stringify(req.body, null, 2)}` : head;
    })
    .join("\n\n");
});

const legend = computed(() =>
  props.variant ? buildPayloadLegend(props.variant, context.value) : [],
);
</script>

<style scoped>
.tp {
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
  border-radius: var(--radius-md);
  padding: var(--space-4);
  overflow-x: auto;
  min-height: 9rem;
}

.tp__label {
  display: block;
  font-size: var(--text-2xs);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  font-weight: var(--weight-semibold);
  opacity: 0.65;
  margin-bottom: var(--space-2);
}

.tp__body {
  margin: 0;
  white-space: pre;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  line-height: var(--leading-body);
}

/* The legend is the point: a URL nobody can decode is not transparency. */
.tp__legend {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--space-2) var(--space-4);
  margin: var(--space-4) 0 0;
  padding: var(--space-4) 0 0;
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  font-size: var(--text-2xs);
}

.tp__legend dt {
  font-family: var(--font-mono);
  color: rgb(var(--v-theme-dark-surface-warning));
}

.tp__legend dd {
  margin: 0;
  opacity: 0.7;
}
</style>
