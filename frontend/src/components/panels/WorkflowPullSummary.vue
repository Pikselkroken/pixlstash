<template>
  <!-- A band under the Workflows toolbar, in the `.wfv-note` family: it is
       the screen telling the reader about something they just did, not a
       dialog they have to answer. It stays until dismissed, because the
       counts are the whole point of the pull and a toast would take them
       away before they were read. -->
  <section
    v-if="pull.phase !== 'idle'"
    class="wfpull"
    aria-label="Pull from ComfyUI"
    data-testid="wfpull"
  >
    <p v-if="pull.phase === 'pulling'" class="wfpull-head" role="status">
      <v-icon size="16" class="wfpull-icon wfpull-icon--quiet" aria-hidden="true"
        >mdi-tray-arrow-down</v-icon
      >
      <span
        >Pulling the workflows ComfyUI has saved. Progress is in the
        <button class="wfpull-link" type="button" @click="showTasks">
          Tasks tab</button
        >.</span
      >
    </p>

    <p v-else-if="pull.phase === 'failed'" class="wfpull-head" role="alert">
      <v-icon
        size="16"
        class="wfpull-icon wfpull-icon--error"
        aria-hidden="true"
        >mdi-close-circle-outline</v-icon
      >
      <span>Couldn't pull from {{ host }}: {{ pull.error }}</span>
      <button class="wfpull-link wfpull-dismiss" type="button" @click="pull.dismiss()">
        Dismiss
      </button>
    </p>

    <template v-else>
      <p class="wfpull-head" role="status">
        <v-icon size="16" class="wfpull-icon wfpull-icon--quiet" aria-hidden="true"
          >mdi-tray-arrow-down</v-icon
        >
        <span class="wfpull-headline">{{ report.headline }}</span>
        <button
          class="wfpull-link wfpull-dismiss"
          type="button"
          @click="pull.dismiss()"
        >
          Dismiss
        </button>
      </p>
      <ul v-if="report.lines.length" class="wfpull-lines">
        <li
          v-for="(line, index) in report.lines"
          :key="index"
          :class="['wfpull-line', `wfpull-line--${line.kind}`]"
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
                <li v-for="name in line.names" :key="name" class="num">
                  {{ name }}
                </li>
              </ul>
            </details>
          </div>
        </li>
      </ul>
    </template>
  </section>
</template>

<script setup>
// What a pull from ComfyUI found (#1440): the store's phase, drawn. The
// sentences are `utils/workflowPull.js`'s, so they are tested without a mount;
// this file owns only the layout and which glyph and ink each kind gets.
import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { useSidebarStore } from "../../stores/useSidebarStore";
import { useWorkflowPullStore } from "../../stores/useWorkflowPullStore";
import { comfyuiHost, pullSummaryLines } from "../../utils/workflowPull";

const pull = useWorkflowPullStore();
const sidebar = useSidebarStore();

const host = computed(() => comfyuiHost(pull.comfyuiUrl));
const report = computed(() => pullSummaryLines(pull.summary, pull.comfyuiUrl));

function showTasks() {
  sidebar.showTasksTab();
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
   have. The unchecked and info kinds share the quiet ink and differ by glyph,
   so "not checked" can never pass for a warning or an all-clear. */
.wfpull-icon {
  flex: none;
  margin-top: 1px;
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
.wfpull-names ul {
  margin: var(--space-1) 0 0;
  padding-left: var(--space-5);
  font-size: var(--text-xs);
}

/* The inline-button reset `.wfv-note-clear` uses: without `font: inherit`
   the button drops to the UA's size. */
.wfpull-link {
  padding: 0;
  font: inherit;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
}
.wfpull-link:hover {
  color: rgb(var(--v-theme-primary));
}
.wfpull-dismiss {
  margin-left: auto;
  flex: none;
}
</style>
