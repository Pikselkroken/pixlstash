<template>
  <AppInspector
    v-model="tab"
    class="tpli"
    label="Inspector"
    :open="sidebarStore.workflowInspectorOpen"
    :tabs="tabs"
  >
    <!-- The task manager, as on Mine: a run started there is still going
         when the reader moves over to browse templates. -->
    <TasksPanel v-if="tab === 'tasks'" />

    <p v-else-if="!chosen.length" class="tpli-empty">
      Pick a template to see what it would download, then add it to your
      workflows.
    </p>

    <div v-else-if="chosen.length > 1" class="inspector-section">
      <span class="section-label">Selected</span>
      <p class="tpli-title">{{ chosen.length }} templates</p>
      <p class="tpli-quiet">
        {{ missingFiles ? `${missingFiles} of their files are not on your shelf.` : "Every file they load is on your shelf." }}
      </p>
    </div>

    <template v-else>
      <div class="inspector-section">
        <div class="tpli-picture">
          <img
            v-if="template.thumbnail && !thumbnailFailed"
            :src="templateThumbnailUrl(template)"
            alt=""
            @error="thumbnailFailed = true"
          />
        </div>
        <p class="tpli-title" data-testid="tpli-title">{{ template.title }}</p>
        <p v-if="template.description" class="tpli-quiet">
          {{ template.description }}
        </p>
      </div>

      <!-- Where it comes from, repeated from the card: nothing here is the
           owner's until it is added. -->
      <div class="inspector-section">
        <div class="tpli-source" data-testid="tpli-source">
          <ComfyuiIcon v-if="template.source === 'builtin'" :size="16" />
          <v-icon v-else size="16">{{
            template.source === "pack" ? "mdi-puzzle-outline" : "mdi-cloud-outline"
          }}</v-icon>
          <div>
            <p class="tpli-source-head">{{ sourceText }}</p>
            <p v-if="templates.reachable" class="tpli-quiet">
              Graph and preview downloaded from your ComfyUI at
              <span class="tpli-mono">{{ templates.comfyuiUrl }}</span>. Nothing
              is stored until you add it.
            </p>
            <p v-else class="tpli-quiet" data-testid="tpli-shipped">
              Nothing is answering at
              <span class="tpli-mono">{{ templates.comfyuiUrl }}</span>, so the
              graph is the copy PixlStash ships and the preview comes from
              ComfyUI's template repository. Added now, it is a draft until
              ComfyUI runs: then <em>Finish in ComfyUI…</em> makes it runnable.
            </p>
            <p v-if="template.pack_loaded === false" class="tpli-warn">
              ComfyUI did not load this pack's nodes, so it cannot run yet.
            </p>
          </div>
        </div>
      </div>

      <div class="inspector-section">
        <div class="tpli-head">
          <span class="section-label">Models</span>
          <span v-if="files.length" class="section-label tpli-count">{{
            `${files.length - missing} of ${files.length} on your shelf`
          }}</span>
        </div>
        <p v-if="!files.length" class="tpli-quiet">
          {{
            template.source === "partner"
              ? partnerText
              : "It loads no model files."
          }}
        </p>
        <ul v-else class="tpli-files">
          <li
            v-for="file in files"
            :key="`${file.directory}/${file.name}`"
            class="tpli-file"
            data-testid="tpli-file"
          >
            <span
              class="tpli-mark"
              :class="{ 'tpli-mark--missing': !file.present }"
              aria-hidden="true"
              >{{ templateFileLetter(file) }}</span
            >
            <span class="tpli-file-text">
              <span class="tpli-file-name" :title="file.name">{{ file.name }}</span>
              <span class="tpli-file-meta tpli-mono">
                {{ file.directory || "models" }} ·
                {{ file.present ? "on your shelf" : "not on your shelf" }}
              </span>
            </span>
            <ModelDownloadButton
              v-if="!file.present && file.downloadable && !isReadOnly"
              class="tpli-dl"
              :file="file.name"
            />
            <a
              v-else-if="!file.present && downloadable(file.url)"
              class="tpli-link"
              :href="file.url"
              target="_blank"
              rel="noopener noreferrer"
              :aria-label="`Where to download ${file.name}`"
            >
              <Tooltip text="Where to download it" activator="parent" />
              <v-icon size="16">mdi-open-in-new</v-icon>
            </a>
          </li>
        </ul>
        <p v-if="sizeText" class="tpli-quiet">
          Downloads {{ sizeText }} in all.
        </p>
      </div>
    </template>

    <template v-if="chosen.length && tab === 'template'" #footer>
      <div class="tpli-foot">
        <AppButton
          variant="primary"
          icon-left="plus"
          block
          :loading="templates.adding"
          data-testid="tpli-add"
          @click="templates.requestAdd()"
        >
          {{
            chosen.length === 1
              ? "Add to my workflows"
              : `Add ${chosen.length} to my workflows`
          }}
        </AppButton>
        <p class="tpli-quiet tpli-footnote">
          Copies the graph from ComfyUI into a new workflow. If PixlStash can
          run it, it runs straight away; if not, it is a draft until you open
          it in ComfyUI and save it there.
        </p>
      </div>
    </template>
  </AppInspector>
</template>

<script setup>
/**
 * The right rail while the Workflows view shows its Templates tab (#2000):
 * what the selected template is, where it comes from, and the files its
 * loaders name, each marked as on the shelf or not. Add is the pill's verb
 * too; both ask the view (`requestAdd`), which lands on Mine afterwards.
 *
 * A missing file PixlStash can fetch (`downloadable`) gets the run's own
 * download button, so it lands in the Tasks tab the same way before the
 * template is added as after; any other one links to where its template says
 * to get it, as does every one in a read-only session, which cannot download.
 * The design's *Download the missing*, all at once, is not here.
 */
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { templateThumbnailUrl } from "../../api/comfyui";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { isReadOnly } from "../../utils/apiClient";
import {
  missingCount,
  templateFileLetter,
  useWorkflowTemplatesStore,
} from "../../stores/useWorkflowTemplatesStore";
import { formatModelSize } from "../../utils/modelShelf";
import AppButton from "../widgets/AppButton.vue";
import AppInspector from "../widgets/AppInspector.vue";
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import ModelDownloadButton from "../widgets/ModelDownloadButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import TasksPanel, { tasksTabFor } from "./TasksPanel.vue";

const sidebarStore = useSidebarStore();
const tasksStore = useTasksStore();
const templates = useWorkflowTemplatesStore();
const tab = ref("template");
// A deep link to the Tasks tab, from a notice or a banner (`showTasksTab`).
watch(
  () => sidebarStore.tasksTabRequest,
  () => (tab.value = "tasks"),
);

const tabs = computed(() => [
  { value: "template", label: "Template", icon: "mdi-sitemap-outline" },
  tasksTabFor(tasksStore),
]);
const thumbnailFailed = ref(false);

const chosen = computed(() => templates.selectedTemplates);
const template = computed(() => chosen.value[0] ?? null);
watch(
  () => template.value?.key,
  () => (thumbnailFailed.value = false),
);

const files = computed(() => template.value?.files ?? []);
const missing = computed(() => files.value.filter((f) => !f.present).length);
const missingFiles = computed(() =>
  chosen.value.reduce((sum, t) => sum + missingCount(t), 0),
);
const sizeText = computed(() => formatModelSize(template.value?.size));

// ComfyUI calls it a premium template that runs with credits.
const partnerText = computed(() => {
  const providers = template.value?.providers ?? [];
  const via = providers.length ? ` through ${providers.join(", ")}` : "";
  return `Runs with credits on Comfy's partner API nodes${via}, and downloads nothing. It needs a Comfy account.`;
});

const sourceText = computed(() => {
  const source = template.value?.source;
  if (source === "builtin") return "ComfyUI built-in template";
  if (source === "pack") return `Template from ${template.value.module}`;
  return "Partner API template";
});

/** Only a web address is a link: the URL is the template author's text. */
function downloadable(url) {
  return typeof url === "string" && /^https:\/\//i.test(url);
}
</script>

<style scoped>
.tpli-empty {
  padding: var(--space-2) 0;
  font-size: var(--text-sm);
}

.tpli-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.tpli-quiet {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.tpli-warn {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
}

.tpli-mono {
  font-family: var(--font-mono);
}

/* The card's 6:5 cover, so the picture is framed the same in both places. */
.tpli-picture {
  overflow: hidden;
  aspect-ratio: 6 / 5;
  margin-bottom: var(--space-3);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
}

.tpli-picture img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: top center;
}

.tpli-source {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.tpli-source p {
  margin: 0;
}

.tpli-source-head {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.tpli-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.tpli-files {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin: var(--space-2) 0;
  padding: 0;
  list-style: none;
}

.tpli-file {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

/* The card's file mark (`WorkflowTemplateCard.vue`), at the row's size. */
.tpli-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  box-sizing: border-box;
  width: var(--entity-thumb);
  height: var(--entity-thumb);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, rgb(var(--v-theme-on-surface)) 12%, transparent);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
}

.tpli-mark--missing {
  border-color: rgb(var(--v-theme-warning));
  background: transparent;
}

.tpli-file-text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.tpli-file-name,
.tpli-file-meta {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tpli-file-name {
  font-size: var(--text-sm);
}

.tpli-file-meta {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The row's gap spaces it; its own margin is for a line of text. */
.tpli-file .tpli-dl {
  margin-left: 0;
}

.tpli-link {
  display: inline-flex;
  flex: none;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.tpli-link:hover {
  color: rgb(var(--v-theme-on-surface));
}

.tpli-foot {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  /* `border`, as the Workflow tab's footer: divider vanishes on the rail. */
  border-top: 1px solid rgb(var(--v-theme-border));
}
</style>
