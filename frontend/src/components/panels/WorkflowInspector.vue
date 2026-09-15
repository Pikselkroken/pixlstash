<template>
  <AppInspector
    v-model="tab"
    class="wfins"
    label="Inspector"
    :open="sidebarStore.statsOpen"
    :tabs="tabs"
  >
    <p v-if="!row && !file" class="wfins-empty">
      Pick a workflow to see what it is made of.
    </p>

    <!-- A saved workflow file: the setup the Workflows view exists for (§F3).
         It has no Pictures tab of its own yet, so this is the whole panel. -->
    <template v-else-if="file">
      <div class="inspector-section">
        <span class="section-label">Selected</span>
        <div class="wfins-field">
          <div class="wfins-name wfins-name--named">
            {{ file.display_name || file.name }}
          </div>
          <div class="wfins-mono">{{ fileLine }}</div>
        </div>
      </div>

      <div class="inspector-section">
        <span class="section-label">Pictures in</span>
        <template v-if="inputsFailed">
          <p class="wfins-quiet wfins-note">
            Could not read this workflow's inputs just now.
          </p>
          <AppButton
            size="sm"
            class="wfins-action"
            @click="store.loadInputs(file.name)"
          >
            Try again
          </AppButton>
        </template>
        <p v-else-if="!setup" class="wfins-quiet wfins-note">
          Reading its inputs…
        </p>
        <p v-else-if="!setup.inputs.length" class="wfins-quiet wfins-note">
          This workflow takes no pictures.
        </p>
        <template v-else>
          <div
            v-for="input in setup.inputs"
            :key="input.node_id"
            class="wfins-input"
          >
            <!-- The graph's own title, which is often "Load Image" twice, so
                 the node id beside it is what maps the input back to the
                 graph. -->
            <div class="wfins-input-head">
              <span class="wfins-input-name">{{ input.title }}</span>
              <span class="wfins-mono">#{{ input.node_id }}</span>
            </div>
            <Segmented
              :options="MODE_OPTIONS"
              :model-value="input.mode"
              full
              :aria-label="`How ${input.title} #${input.node_id} is filled`"
              @update:model-value="(mode) => chooseMode(input, mode)"
            />
            <div v-if="input.mode === 'fixed'" class="wfins-fixed">
              <img
                v-if="input.picture_id"
                class="wfins-fixed-thumb"
                :src="thumbUrl(input.picture_id)"
                :alt="`The picture fixed for ${input.title} #${input.node_id}`"
              />
              <span v-else class="wfins-fixed-thumb wfins-fixed-thumb--missing">
                <v-icon size="18">mdi-image-off-outline</v-icon>
              </span>
              <span class="wfins-note wfins-fixed-text">{{
                input.picture_missing
                  ? "Its picture is no longer in this library."
                  : "Used for every run."
              }}</span>
              <AppButton
                size="sm"
                :aria-label="`Change the picture for ${input.title} #${input.node_id}`"
                @click="pickerFor = input"
              >
                Change
              </AppButton>
            </div>
            <p class="wfins-quiet wfins-note">{{ MODE_NOTES[input.mode] }}</p>
          </div>
          <p v-if="!takesSelection" class="wfins-note">
            No input takes the selection, so this workflow is offered from the
            toolbar's Generate button, not on a selection.
          </p>
        </template>
      </div>

      <!-- A built-in workflow ships with the app and has nothing to delete. -->
      <div v-if="file.source !== 'built-in'" class="inspector-section">
        <AppButton
          size="sm"
          variant="danger"
          icon-left="delete-outline"
          class="wfins-action"
          :disabled="deleting"
          @click="deleteFile"
        >
          Delete workflow
        </AppButton>
      </div>

      <PicturePicker
        :open="pickerFor !== null"
        :subtitle="pickerFor ? `for ${pickerFor.title} #${pickerFor.node_id}` : ''"
        @close="pickerFor = null"
        @pick="onPicked"
      />
    </template>

    <template v-else-if="tab === 'workflow'">
      <div class="inspector-section">
        <span class="section-label">Selected</span>
        <div class="wfins-field">
          <div class="wfins-name">{{ descriptor }}</div>
          <div class="wfins-mono">
            {{ variantLine }}<template v-if="base"> · {{ base }}</template>
          </div>
        </div>
      </div>

      <div class="inspector-section">
        <span class="section-label">Details</span>
        <dl class="inspector-kv">
          <div>
            <dt>Nodes</dt>
            <dd>{{ groupedNumber(row.node_count) }}</dd>
          </div>
          <div>
            <dt>Variants</dt>
            <dd>{{ groupedNumber(row.variants) }}</dd>
          </div>
          <div>
            <dt>Pictures</dt>
            <dd>
              <span v-if="row.pictures">{{ groupedNumber(row.pictures) }}</span>
              <span v-else class="wfins-quiet">none kept</span>
            </dd>
          </div>
          <div>
            <dt>Last used</dt>
            <dd>
              <span v-if="row.last_used">{{ day(row.last_used) }}</span>
              <span v-else class="wfins-quiet">—</span>
            </dd>
          </div>
          <div>
            <dt>First seen</dt>
            <dd>{{ day(row.first_seen_at) }}</dd>
          </div>
        </dl>
      </div>

      <div class="inspector-section">
        <span class="section-label">Models</span>
        <div v-if="models.length" class="wfins-chips">
          <span v-for="model in models" :key="model.name" class="wfins-chip"
            ><Tooltip :text="model.name" activator="parent" />{{
              modelStem(model.name)
            }}</span
          >
        </div>
        <!-- Empty is a state, and it says only what is true. `forgotten_models`
               is what separates forgotten names from a graph that names no
               model, so only a row that carries it says they were forgotten. -->
        <p v-if="row.forgotten_models" class="wfins-quiet wfins-note">
          {{ forgottenNote }} The graph still says a model goes there, and no
          longer says which.
        </p>
        <p v-else-if="!models.length" class="wfins-quiet wfins-note">
          This workflow's model names are not recorded.
        </p>
      </div>

      <div class="inspector-section">
        <span class="section-label">Variants</span>
        <div v-if="variantBars.length" class="wfins-bars">
          <div v-for="bar in variantBars" :key="bar.key" class="wfins-bar">
            <span class="wfins-bar-label" :title="bar.label">{{
              bar.label
            }}</span>
            <span class="wfins-bar-track">
              <span class="wfins-bar-fill" :style="{ width: bar.width }"></span>
              <span class="wfins-bar-value num">{{
                groupedNumber(bar.value)
              }}</span>
            </span>
          </div>
        </div>
        <AppButton
          v-else-if="row.variants > 1"
          size="sm"
          class="wfins-action"
          @click="store.toggleOpen(row.topology_hash)"
        >
          Read its {{ row.variants }} variants
        </AppButton>
        <p v-else class="wfins-quiet wfins-note">
          One variant: this graph was only ever bound to one set of models.
        </p>
      </div>
    </template>

    <template v-else>
      <div class="inspector-section">
        <span class="section-label">Made with it</span>
        <div v-if="sampleIds.length" class="wfins-tiles">
          <button
            v-for="id in sampleIds"
            :key="id"
            class="wfins-tile"
            type="button"
            :aria-label="`Open picture ${id}`"
            @click="openPicture(id)"
          >
            <Tooltip
              :text="`Open picture ${id}`"
              activator="parent"
              :describe="false"
            />
            <img :src="thumbUrl(id)" alt="" loading="lazy" />
          </button>
        </div>
        <!-- Three sentences, not one, because the difference matters: this
               workflow outlived its pictures, we have not read them yet, or we
               tried and could not. Saying "nothing is left" when the request
               simply failed is the one thing this panel must not do — and
               saying "reading…" for ever when it already came back empty-handed
               is how the first version of that fix failed. -->
        <p v-else-if="samplesPending" class="wfins-quiet wfins-note">
          Reading its pictures…
        </p>
        <template v-else-if="samplesFailed">
          <p class="wfins-quiet wfins-note">
            Could not read its pictures just now.<template v-if="hasPictures">
              The library still has {{ groupedNumber(row.pictures) }} of
              them.</template
            >
          </p>
          <!-- The failure has a way out of it. Without this the only retry is
                 selecting another workflow and coming back, which nothing on
                 screen tells anybody. -->
          <AppButton
            size="sm"
            class="wfins-action"
            @click="store.loadSamples(row.topology_hash)"
          >
            Try again
          </AppButton>
        </template>
        <p v-else class="wfins-quiet wfins-note">
          Nothing this workflow made is still in the library. The workflow
          itself is kept whole.
        </p>
      </div>
    </template>
  </AppInspector>
</template>

<script setup>
// The workflow inspector (implementation plan §F2).
//
// The kit already says the inspector is one component with three uses; this is
// the fourth and it needs no new shell — in Workflows the right rail carries
// the selected workflow instead of the library's statistics. It is a separate
// component rather than a branch inside `StatsSidebar.vue` for the same reason
// the shelf replaces the grid rather than floating over it: the statistics
// panel fetches on watchers, and a hidden-but-mounted one would keep asking for
// numbers nobody is looking at.
//
// The two rules the design record draws out of the artboards are both here. The
// tab strip names the SUBJECT — Workflow, where Model and Pictures sit — and the
// second tab is always the way out to the pictures, which is what stops the
// rail being terminal. It dims rather than disappearing when a workflow has
// outlived everything it made, so the panel keeps its shape.

import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { deleteWorkflow } from "../../api/comfyui";
import { pictureThumbnailUrl } from "../../api/pictures";
import { useConfirm } from "../../composables/useConfirm";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useWorkflowShelfStore } from "../../stores/useWorkflowShelfStore";
import { errorDetail } from "../../utils/apiError";
import { formatUserDay } from "../../utils/utils";
import {
  baseModelName,
  groupedNumber,
  modelAssets,
  modelStem,
  modelSummary,
  workflowDescriptor,
} from "../../utils/workflowShelf";
import AppButton from "../widgets/AppButton.vue";
import AppInspector from "../widgets/AppInspector.vue";
import PicturePicker from "../widgets/PicturePicker.vue";
import Segmented from "../widgets/Segmented.vue";
import Tooltip from "../widgets/Tooltip.vue";

// The three ways a picture input is filled, in the design's words.
const MODE_OPTIONS = [
  { id: "selection", label: "Selection" },
  { id: "picker", label: "Picker" },
  { id: "fixed", label: "Fixed" },
];

const MODE_NOTES = {
  selection:
    "Filled by whatever is selected in the grid. One input may hold this, and it sets the run count.",
  picker: "Asked each time the workflow runs.",
  fixed:
    "Chosen once, here. Shown but not editable when the workflow runs.",
};

const store = useWorkflowShelfStore();
const notices = useNoticeStore();
const sidebarStore = useSidebarStore();
const userPrefs = useUserPrefsStore();
const router = useRouter();
const { confirm } = useConfirm();

const tab = ref("workflow");

const row = computed(() => store.selectedRow);
const file = computed(() => store.selectedFileRow);
const setup = computed(() =>
  file.value ? store.inputs[file.value.name] || null : null,
);
const inputsFailed = computed(() =>
  file.value ? store.inputsDidFail(file.value.name) : false,
);
const takesSelection = computed(() =>
  (setup.value?.inputs || []).some((input) => input.mode === "selection"),
);
/** The input the picture picker is open for, or null. */
const pickerFor = ref(null);

const fileLine = computed(() => {
  if (!file.value) return "";
  const n = setup.value?.inputs.length;
  const count =
    n == null ? "" : `${n} ${n === 1 ? "picture" : "pictures"} in · `;
  return `${count}${file.value.source === "built-in" ? "built in" : "yours"}`;
});
const descriptor = computed(() =>
  row.value ? workflowDescriptor(row.value) : "",
);
const base = computed(() =>
  row.value ? baseModelName(row.value.assets) : null,
);
const models = computed(() => (row.value ? modelAssets(row.value.assets) : []));

const variantLine = computed(() => {
  const n = Number(row.value?.variants) || 0;
  return `${groupedNumber(n)} ${n === 1 ? "variant" : "variants"}`;
});

const sampleIds = computed(() =>
  row.value ? store.samples[row.value.topology_hash] || [] : [],
);

const hasPictures = computed(() => Boolean(row.value?.pictures));

// The tab strip names the SUBJECT, never the view, so Workflow sits where Model
// and Pictures sit, and the second tab is always the way out to the pictures,
// which is what makes the rail navigable rather than terminal.
const tabs = computed(() => [
  { value: "workflow", label: "Workflow", icon: "mdi-sitemap-outline" },
  {
    value: "pictures",
    label: "Pictures",
    icon: "mdi-image-multiple-outline",
    disabled: !hasPictures.value,
    tooltip: hasPictures.value
      ? ""
      : file.value
        ? "A saved workflow is not linked to the pictures it made yet"
        : "Nothing this workflow made is still in the library",
  },
]);

/**
 * Whether the tiles are still on their way.
 *
 * A missing `samples[hash]` is NOT enough to answer this: absent means both
 * "not asked yet" and "asked and failed", and reading it as pending leaves the
 * panel saying "Reading its pictures…" for ever after one dropped request,
 * with the sentence written for the failure unreachable. The store records the
 * failure separately, so the two are told apart here.
 */
const samplesFailed = computed(() =>
  row.value ? store.samplesDidFail(row.value.topology_hash) : false,
);

const samplesPending = computed(() => {
  if (!row.value) return false;
  const hash = row.value.topology_hash;
  if (store.isSamplesLoading(hash)) return true;
  return !(hash in store.samples) && !samplesFailed.value;
});

/**
 * The variants, as a magnitude ramp.
 *
 * Only drawn once the variants have actually been read: a bar chart of what the
 * rail guessed would be a different claim from a bar chart of what the library
 * holds. Until then the section offers to read them, which is the same request
 * the row's own disclosure makes.
 */
/** What the Models section says when a variant's names were forgotten. */
const forgottenNote = computed(() => {
  const n = Number(row.value?.forgotten_models) || 0;
  if (models.value.length) return "Some of its model names were forgotten.";
  return n === 1
    ? "1 model's name was forgotten."
    : `${n} models' names were forgotten.`;
});

const variantBars = computed(() => {
  const list = row.value ? store.variants[row.value.topology_hash] : null;
  if (!Array.isArray(list) || !list.length) return [];
  const top = Math.max(...list.map((v) => Number(v.pictures) || 0), 1);
  return [...list]
    .sort((a, b) => (Number(b.pictures) || 0) - (Number(a.pictures) || 0))
    .map((variant) => ({
      key: variant.structural_hash,
      label:
        modelSummary(variant.assets, 2, variant.forgotten_models) ||
        "not named",
      value: Number(variant.pictures) || 0,
      width: `${Math.round(((Number(variant.pictures) || 0) / top) * 100)}%`,
    }));
});

/**
 * Choose how an input is filled. Fixed needs its picture first, so it opens the
 * picker and changes nothing until one is chosen.
 */
async function chooseMode(input, mode) {
  // Never disabled while a write is out, which would drop keyboard focus on
  // every arrow step; the store queues the step instead. Fixed asks for a
  // picture only when the input has none to go back to.
  if (mode === "fixed" && !input.picture_id) {
    pickerFor.value = input;
    return;
  }
  const failed = await store.setInputMode(file.value.name, input.node_id, mode);
  if (failed) notices.push({ level: "error", text: failed });
}

// The picker stays open when the write fails, so the choice can be retried
// without finding the picture again.
async function onPicked(picture) {
  const input = pickerFor.value;
  if (!input || !file.value) return;
  const failed = await store.setInputMode(
    file.value.name,
    input.node_id,
    "fixed",
    picture.id,
  );
  if (failed) notices.push({ level: "error", text: failed });
  else pickerFor.value = null;
}

const deleting = ref(false);

/**
 * Delete the selected saved workflow. The server writes it back to the
 * workflows folder and moves that file to the system trash, so restoring it
 * from there imports it again.
 */
async function deleteFile() {
  const target = file.value;
  if (!target) return;
  const label = target.display_name || target.name;
  const confirmed = await confirm({
    title: "Delete workflow",
    message: `Delete '${label}'? It goes to the system trash, and restoring it from there adds it back.`,
    confirmLabel: "Delete",
    danger: true,
  });
  if (!confirmed) return;
  deleting.value = true;
  try {
    await deleteWorkflow(target.name);
    await store.fetchFiles();
  } catch (err) {
    console.warn("[workflows] could not delete", target.name, err);
    notices.push({
      level: "error",
      text: errorDetail(err) || `Could not delete '${label}'.`,
    });
  } finally {
    deleting.value = false;
  }
}

function day(iso) {
  return iso ? formatUserDay(iso, userPrefs.dateFormat) : "";
}

function thumbUrl(id) {
  return pictureThumbnailUrl(id);
}

/**
 * Leave for the picture itself.
 *
 * `?overlay=<id>` on the library route is the shipped way to open one — it is
 * how a reloaded lightbox restores itself — so this reuses that rather than
 * inventing a second route into the viewer.
 */
function openPicture(id) {
  router.push({ name: "all-pictures", query: { overlay: String(id) } });
}

// A workflow that has outlived its pictures cannot show the Pictures tab, and a
// rail left on it would read as a fetch that failed. Selecting a new workflow
// also asks for its tiles, which the store fetches once per workflow.
// Read every time a file is selected: it can be replaced under the same name.
watch(
  () => file.value?.name,
  (name) => {
    pickerFor.value = null;
    if (!name) return;
    tab.value = "workflow";
    store.loadInputs(name);
  },
  { immediate: true },
);

watch(
  () => row.value?.topology_hash,
  (hash) => {
    if (!hash || !hasPictures.value) tab.value = "workflow";
    if (hash) store.loadSamples(hash);
  },
  { immediate: true },
);
</script>

<style scoped>
.wfins-field {
  background: rgba(var(--v-theme-on-surface), 0.04);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
  padding: var(--space-3);
}

.wfins-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  font-style: italic;
}

.wfins-name--named {
  font-style: normal;
}

.wfins-input {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.wfins-input + .wfins-input {
  padding-top: var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.wfins-input-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  min-width: 0;
}

.wfins-input-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wfins-fixed {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.wfins-fixed-thumb {
  width: var(--space-8);
  height: var(--space-8);
  flex: none;
  border-radius: var(--radius-sm);
  object-fit: cover;
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.wfins-fixed-thumb--missing {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfins-fixed-text {
  flex: 1;
  min-width: 0;
}

.wfins-mono {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfins-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.wfins-chip {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  padding: 2px var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.04);
  font-size: var(--text-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wfins-bars {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.wfins-bar {
  display: grid;
  grid-template-columns: 5.5rem 1fr;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-xs);
}

.wfins-bar-label {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: right;
}

.wfins-bar-track {
  position: relative;
  height: 18px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.07);
  overflow: hidden;
}

.wfins-bar-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-accent), 0.75);
}

.wfins-bar-value {
  position: absolute;
  right: var(--space-3);
  top: 0;
  line-height: 18px;
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
}

.wfins-tiles {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--space-2);
}

.wfins-tile {
  position: relative;
  aspect-ratio: 1 / 1;
  padding: 0;
  border: 0;
  border-radius: var(--radius-md);
  overflow: hidden;
  background: rgba(var(--v-theme-on-surface), 0.06);
  cursor: pointer;
}

.wfins-tile img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.wfins-action {
  align-self: flex-start;
}

.wfins-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfins-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
}

.wfins-empty {
  margin: 0;
  padding: var(--space-2) 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
