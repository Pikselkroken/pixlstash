<template>
  <!-- ONE live region, mounted for as long as the screen is: a region that
       arrives already holding its text is skipped by several screen readers,
       so the band's state is WRITTEN into this one rather than mounted with
       its own. Polling does not repeat it - the text only changes when the
       phase does. -->
  <p class="visually-hidden" role="status">{{ spoken }}</p>

  <!-- A band under the Workflows toolbar, in the `.wfv-note` family: it is
       the screen telling the reader about something they just did, not a
       dialog they have to answer. It stays until dismissed, because the
       counts are the point of the pull and a toast would take them away
       before they were read. -->
  <div
    v-if="pull.phase === 'done'"
    class="wfpull"
    data-testid="wfpull"
  >
    <p class="wfpull-head">
      <v-icon size="16" class="wfpull-icon wfpull-icon--quiet" aria-hidden="true"
        >mdi-tray-arrow-down</v-icon
      >
      <span class="wfpull-headline">{{ report.headline }}</span>
      <button
        class="wfpull-link"
        type="button"
        aria-label="Dismiss the ComfyUI pull result"
        @click="dismiss"
      >
        Dismiss
      </button>
    </p>
    <ul v-if="report.lines.length" class="wfpull-lines">
      <li
        v-for="(line, index) in report.lines"
        :key="index"
        class="wfpull-line"
        :data-kind="line.kind"
      >
        <v-icon
          size="16"
          :class="['wfpull-icon', `wfpull-icon--${line.kind}`]"
          aria-hidden="true"
          >{{ `mdi-${line.icon}` }}</v-icon
        >
        <div class="wfpull-body">
          <span>{{ line.text }}</span>
          <details v-if="line.names && line.names.length" class="wfpull-names">
            <summary>{{ line.namesLabel }} ({{ line.names.length }})</summary>
            <ul>
              <li v-for="name in line.names" :key="name">{{ name }}</li>
            </ul>
          </details>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup>
// What a pull from ComfyUI found (#1440): the store's phase, drawn. The
// sentences are `utils/workflowPull.js`'s, so they are tested without a mount;
// this file owns only the layout, which glyph and ink each kind gets, and the
// one live region.
import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { useWorkflowPullStore } from "../../stores/useWorkflowPullStore";
import { pullSummaryLines } from "../../utils/workflowPull";

// The band unmounts on Dismiss, which would drop focus to <body>; the host
// puts it back somewhere sensible.
const emit = defineEmits(["dismissed"]);

const pull = useWorkflowPullStore();

const report = computed(() =>
  pullSummaryLines(pull.summary, pull.comfyuiUrl),
);

const spoken = computed(() =>
  pull.phase === "done" ? report.value.headline : "",
);

function dismiss() {
  pull.dismiss();
  emit("dismissed");
}
</script>

<style scoped>
.wfpull {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-sm);
}

.wfpull-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
}

.wfpull-headline {
  font-weight: var(--weight-semibold);
}

.wfpull-lines {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.wfpull-line {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
}

.wfpull-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

/* The glyph carries the kind, so the text stays in the surface's own ink:
   status hue as a glyph on the canvas is `surface-<status>` (visual language
   §4), never `on-<status>`, which is for a solid fill this band does not
   have. Unchecked and info share the quiet ink and differ by glyph, so "not
   checked" can never pass for a warning or an all-clear. */
.wfpull-icon {
  flex: none;
}
.wfpull-icon--error {
  color: rgb(var(--v-theme-surface-error));
}
.wfpull-icon--warning {
  color: rgb(var(--v-theme-surface-warning));
}
.wfpull-icon--unchecked,
.wfpull-icon--info,
.wfpull-icon--quiet {
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.wfpull-names summary {
  cursor: pointer;
  color: rgba(var(--v-theme-on-surface), 0.6);
}
/* Node classes and model files are identifiers, so they are set in the mono
   family (visual language §3), which also keeps `_` and `-` legible. */
.wfpull-names ul {
  margin: var(--space-1) 0 0;
  padding-left: var(--space-5);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

/* `.wfv-note-clear`'s inline-button reset, repeated because that class is
   scoped to the view: without `font: inherit` the button drops to the UA's
   size. */
.wfpull-link {
  margin-left: auto;
  flex: none;
  padding: 0;
  font: inherit;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
}
.wfpull-link:hover {
  color: rgb(var(--v-theme-primary));
}
</style>
