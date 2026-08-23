<template>
  <div class="wfins" :class="{ collapsed: !sidebarStore.statsOpen }">
    <div v-if="sidebarStore.statsOpen" class="wfins-content">
      <div class="wfins-header">
        <div class="wfins-title-row">
          <span class="wfins-title-text">
            <v-icon size="13" class="wfins-title-icon"
              >mdi-sitemap-outline</v-icon
            >
            Inspector
          </span>
        </div>
        <!-- The tab strip names the SUBJECT, never the view, so Workflow sits
             where Model and Pictures sit — and the second tab is always the way
             out to the pictures, which is what makes the rail navigable rather
             than terminal. -->
        <div class="wfins-tabs">
          <button
            class="wfins-tab-btn"
            :class="{ active: tab === 'workflow' }"
            type="button"
            @click="tab = 'workflow'"
          >
            <v-icon size="12">mdi-sitemap-outline</v-icon>
            Workflow
          </button>
          <button
            class="wfins-tab-btn"
            :class="{ active: tab === 'pictures' }"
            type="button"
            :disabled="!hasPictures"
            :title="
              hasPictures
                ? undefined
                : 'Nothing this workflow made is still in the library'
            "
            @click="tab = 'pictures'"
          >
            <v-icon size="12">mdi-image-multiple-outline</v-icon>
            Pictures
          </button>
        </div>
      </div>

      <p v-if="!row" class="wfins-empty">
        Pick a workflow to see what it is made of.
      </p>

      <template v-else-if="tab === 'workflow'">
        <div class="wfins-section">
          <span class="wfins-section-title">Selected</span>
          <div class="wfins-field">
            <div class="wfins-name">{{ descriptor }}</div>
            <div class="wfins-mono">
              {{ variantLine }}<template v-if="base"> · {{ base }}</template>
            </div>
          </div>
        </div>

        <div class="wfins-section">
          <span class="wfins-section-title">Details</span>
          <dl class="wfins-kv">
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
                <span v-if="row.pictures">{{
                  groupedNumber(row.pictures)
                }}</span>
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

        <div class="wfins-section">
          <span class="wfins-section-title">Models</span>
          <div v-if="models.length" class="wfins-chips">
            <span
              v-for="model in models"
              :key="model.name"
              class="wfins-chip"
              :title="model.name"
              >{{ modelStem(model.name) }}</span
            >
          </div>
          <!-- Empty is a state, and it says only what is true. A recipe whose
               asset rows were deleted keeps its graph and loses the ability to
               say which models it used — but nothing in the payload separates
               that from a graph that names no model, so neither is claimed. -->
          <p v-else class="wfins-quiet wfins-note">
            This workflow's model names are not recorded. The graph still says a
            model goes here, and no longer says which.
          </p>
        </div>

        <div class="wfins-section">
          <span class="wfins-section-title">Variants</span>
          <div v-if="variantBars.length" class="wfins-bars">
            <div v-for="bar in variantBars" :key="bar.key" class="wfins-bar">
              <span class="wfins-bar-label" :title="bar.label">{{
                bar.label
              }}</span>
              <span class="wfins-bar-track">
                <span
                  class="wfins-bar-fill"
                  :style="{ width: bar.width }"
                ></span>
                <span class="wfins-bar-value num">{{
                  groupedNumber(bar.value)
                }}</span>
              </span>
            </div>
          </div>
          <button
            v-else-if="row.variants > 1"
            class="wfins-action"
            type="button"
            @click="store.toggleOpen(row.topology_hash)"
          >
            Read its {{ row.variants }} variants
          </button>
          <p v-else class="wfins-quiet wfins-note">
            One variant: this graph was only ever bound to one set of models.
          </p>
        </div>
      </template>

      <template v-else>
        <div class="wfins-section">
          <span class="wfins-section-title">Made with it</span>
          <div v-if="sampleIds.length" class="wfins-tiles">
            <button
              v-for="id in sampleIds"
              :key="id"
              class="wfins-tile"
              type="button"
              :title="`Open picture ${id}`"
              @click="openPicture(id)"
            >
              <img :src="thumbUrl(id)" alt="" loading="lazy" />
            </button>
          </div>
          <!-- Three sentences, not one, because the difference matters: this
               workflow outlived its pictures, we have not read them yet, or we
               tried and could not. Saying "nothing is left" when the request
               simply failed is the one thing this panel must not do. -->
          <p v-else-if="samplesPending" class="wfins-quiet wfins-note">
            Reading its pictures…
          </p>
          <p v-else-if="hasPictures" class="wfins-quiet wfins-note">
            Could not read its pictures just now. The library still has
            {{ groupedNumber(row.pictures) }} of them.
          </p>
          <p v-else class="wfins-quiet wfins-note">
            Nothing this workflow made is still in the library. The workflow
            itself is kept whole.
          </p>
        </div>
      </template>
    </div>
  </div>
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

import { pictureThumbnailUrl } from "../../api/pictures";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useWorkflowShelfStore } from "../../stores/useWorkflowShelfStore";
import { formatUserDay } from "../../utils/utils";
import {
  baseModelName,
  groupedNumber,
  modelAssets,
  modelStem,
  modelSummary,
  workflowDescriptor,
} from "../../utils/workflowShelf";

const store = useWorkflowShelfStore();
const sidebarStore = useSidebarStore();
const userPrefs = useUserPrefsStore();
const router = useRouter();

const tab = ref("workflow");

const row = computed(() => store.selectedRow);
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

/**
 * Whether the tiles are still on their way.
 *
 * `samples[hash]` being absent means one of two things — not asked yet, or
 * asked and failed — and the tab has a different sentence for each.
 */
const samplesPending = computed(() =>
  row.value
    ? store.isSamplesLoading(row.value.topology_hash) ||
      !(row.value.topology_hash in store.samples)
    : false,
);

/**
 * The variants, as a magnitude ramp.
 *
 * Only drawn once the variants have actually been read: a bar chart of what the
 * rail guessed would be a different claim from a bar chart of what the library
 * holds. Until then the section offers to read them, which is the same request
 * the row's own disclosure makes.
 */
const variantBars = computed(() => {
  const list = row.value ? store.variants[row.value.topology_hash] : null;
  if (!Array.isArray(list) || !list.length) return [];
  const top = Math.max(...list.map((v) => Number(v.pictures) || 0), 1);
  return [...list]
    .sort((a, b) => (Number(b.pictures) || 0) - (Number(a.pictures) || 0))
    .map((variant) => ({
      key: variant.structural_hash,
      label: modelSummary(variant.assets, 2) || "not named",
      value: Number(variant.pictures) || 0,
      width: `${Math.round(((Number(variant.pictures) || 0) / top) * 100)}%`,
    }));
});

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
/* The stats rail's own box, so the two panels present the same edge onto the
   canvas and the same collapse. */
.wfins {
  position: relative;
  width: var(--stats-panel-w);
  min-width: var(--stats-panel-w);
  max-width: var(--stats-panel-w);
  height: 100%;
  display: flex;
  flex-direction: row;
  flex-shrink: 0;
  border-left: 1px solid rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-sidebar));
  transition:
    width var(--dur-1) var(--ease-standard),
    min-width var(--dur-1) var(--ease-standard),
    border-color var(--dur-1) var(--ease-standard);
  overflow: hidden;
}

.wfins.collapsed {
  width: 0;
  min-width: 0;
  max-width: 0;
  border-left-color: transparent;
  overflow: hidden;
}

.wfins-content {
  flex: 1;
  min-width: 0;
  padding: 0 var(--space-3) var(--space-4) var(--space-3);
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.wfins-header {
  display: flex;
  flex-direction: column;
  margin-bottom: var(--space-2);
  /* The toolbar's height, so the three header bands line up across the app. */
  height: 36px;
  flex-shrink: 0;
}

.wfins-title-row {
  display: flex;
  align-items: center;
  flex: 1;
  padding: 0 var(--space-2) 0 var(--space-3);
}

.wfins-title-text {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.wfins-title-icon {
  color: rgba(var(--v-theme-on-surface), 0.4);
}

.wfins-tabs {
  display: flex;
  align-items: stretch;
  flex-shrink: 0;
}

.wfins-tab-btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-medium);
  padding: 0 var(--space-3);
  background: none;
  border: 0;
  border-bottom: 2px solid transparent;
  color: rgba(var(--v-theme-on-surface), 0.45);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  cursor: pointer;
  transition:
    color var(--dur-1) var(--ease-standard),
    border-color var(--dur-1) var(--ease-standard);
}

.wfins-tab-btn:hover:not(:disabled) {
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.wfins-tab-btn.active {
  color: rgba(var(--v-theme-primary), 1);
  border-bottom-color: rgba(var(--v-theme-primary), 0.85);
}

/* Dimmed rather than removed: the panel keeps its shape when a workflow has
   outlived everything it made. */
.wfins-tab-btn:disabled {
  opacity: var(--opacity-disabled);
  cursor: default;
}

.wfins-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.wfins-section:last-child {
  border-bottom: 0;
}

.wfins-section-title {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), 0.45);
}

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

.wfins-mono {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfins-kv {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4) var(--space-3);
  margin: 0;
}

.wfins-kv dt {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfins-kv dd {
  margin: 0;
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
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
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.07);
  overflow: hidden;
}

.wfins-bar-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: var(--radius-sm);
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

.wfins-tile:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}

.wfins-action {
  align-self: flex-start;
  padding: var(--space-2) var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font-size: var(--text-xs);
  cursor: pointer;
}

.wfins-action:hover {
  background: var(--hover-wash);
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
  padding: var(--space-4) var(--space-3);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
