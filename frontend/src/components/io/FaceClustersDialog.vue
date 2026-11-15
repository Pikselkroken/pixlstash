<template>
  <AppDialog
    :open="open"
    :title="`${name}'s faces`"
    :subtitle="subtitle"
    size="2xl"
    :pad-body="false"
    :persistent="saving || zoomedId != null"
    @close="close"
  >
    <template #header-right>
      <Segmented
        :model-value="strictness"
        :options="STRICTNESS"
        aria-label="Strictness"
        data-testid="fc-strictness"
        @update:model-value="setStrictness"
      />
    </template>

    <div class="fc">
      <p v-if="error" class="fc-notice" role="alert">
        <v-icon size="16">mdi-alert-circle-outline</v-icon><span>{{ error }}</span>
      </p>
      <p v-else-if="!data" class="fc-note fc-pad">Grouping the faces…</p>
      <p v-else-if="!clusters.length" class="fc-note fc-pad" data-testid="fc-none">
        {{
          data.faces
            ? `No two of ${name}'s faces look alike at ${strictnessLabel}. Try Loose.`
            : `${name} has no pictures with a face yet.`
        }}
      </p>

      <div v-else class="fc-split">
        <div class="fc-rail">
          <span class="fc-lbl fc-lbl--rail" :id="`${uid}-rail`">Clusters</span>
          <div
            class="fc-rows"
            role="listbox"
            tabindex="0"
            :aria-labelledby="`${uid}-rail`"
            :aria-activedescendant="`${uid}-row-${selected}`"
            data-testid="fc-rail"
            @keydown.down.prevent="select(Math.min(selected + 1, clusters.length - 1))"
            @keydown.up.prevent="select(Math.max(selected - 1, 0))"
          >
            <div
              v-for="(c, i) in clusters"
              :id="`${uid}-row-${i}`"
              :key="i"
              class="fc-row"
              role="option"
              :aria-selected="i === selected"
              :aria-label="rowName(c, i)"
              data-testid="fc-row"
              @click="select(i)"
            >
              <img
                class="fc-row__face"
                :src="pictureThumbnailUrl(c.members[0].picture_id)"
                alt=""
              />
              <span class="fc-row__text">
                <span class="fc-row__name"
                  >Cluster {{ i + 1 }}
                  <span v-if="c.is_reference" class="fc-badge fc-badge--ref"
                    ><v-icon size="12">mdi-check</v-icon>Reference</span
                  >
                  <span v-if="c.from_sheets" class="fc-badge fc-badge--new"
                    >New · from sheets</span
                  ></span
                >
                <span class="fc-row__meta"
                  >{{ c.size }} pictures · holds together at
                  {{ fixed(c.cohesion) }}</span
                >
                <span
                  v-if="c.similarity_to_reference != null"
                  class="fc-meter"
                  aria-hidden="true"
                  ><i :style="{ width: `${meterOf(c)}%` }"
                /></span>
              </span>
            </div>
          </div>
          <div v-if="unclustered.length" class="fc-rail__foot">
            <span class="fc-lbl">Matched no one · {{ unclustered.length }}</span>
            <span class="fc-strip">
              <img
                v-for="u in unclustered.slice(0, 24)"
                :key="u.picture_id"
                :src="pictureThumbnailUrl(u.picture_id)"
                alt=""
              />
            </span>
          </div>
        </div>

        <section class="fc-pane" :aria-label="`Cluster ${selected + 1}`">
          <div class="fc-head">
            <h3 class="fc-title">
              Cluster {{ selected + 1 }}
              <span v-if="current.is_reference" class="fc-badge fc-badge--ref"
                ><v-icon size="12">mdi-check</v-icon>Reference</span
              >
            </h3>
            <span class="fc-meta" data-testid="fc-count"
              ><b>{{ kept.length }}</b> of {{ current.size }} pictures count ·
              holds together at {{ fixed(current.cohesion) }}</span
            >
            <template v-if="current.is_reference">
              <AppButton
                variant="secondary"
                :disabled="saving"
                data-testid="fc-clear"
                @click="save([])"
                >Clear</AppButton
              >
              <AppButton
                v-if="changed"
                variant="primary"
                :loading="saving"
                data-testid="fc-save"
                @click="save(kept)"
                >Save {{ kept.length }} faces as the reference</AppButton
              >
            </template>
            <AppButton
              v-else
              variant="primary"
              :loading="saving"
              :disabled="!kept.length"
              data-testid="fc-pick"
              @click="save(kept)"
              >Make this {{ name }}'s reference cluster</AppButton
            >
          </div>

          <div class="fc-body">
            <div class="fc-fieldwrap">
              <div
                ref="fieldEl"
                class="fc-field"
                role="group"
                :aria-label="`Faces of cluster ${selected + 1}, most typical first`"
                :style="{ width: `${layout.width}px`, height: `${layout.height}px` }"
                data-testid="fc-field"
                @keydown="onFieldKey"
              >
                <button
                  v-for="(f, rank) in layout.items"
                  :key="f.picture_id"
                  type="button"
                  class="fc-face"
                  :class="{ 'fc-face--out': isOut(rank) }"
                  :style="{
                    left: `${f.x}px`,
                    top: `${f.y}px`,
                    width: `${f.size}px`,
                    height: `${f.size}px`,
                  }"
                  :tabindex="rank === focusRank ? 0 : -1"
                  :aria-label="faceName(rank)"
                  :aria-keyshortcuts="'Z'"
                  :data-rank="rank"
                  data-testid="fc-face"
                  @focus="focusRank = rank"
                  @click="openZoom(f.picture_id)"
                  @wheel="onTileWheel($event, f.picture_id)"
                >
                  <img :src="pictureThumbnailUrl(f.picture_id)" alt="" loading="lazy" />
                  <span class="fc-chip fc-chip--lk">{{ fixed(shown[rank].centrality) }}</span>
                  <span v-if="shown[rank].score" class="fc-chip fc-chip--sc"
                    >{{ shown[rank].score }}★</span
                  >
                </button>
              </div>
              <span v-if="hidden" class="fc-note">+{{ hidden }} more, less typical</span>
            </div>

            <aside class="fc-side">
              <div v-if="comparison" class="fc-card" data-testid="fc-compare">
                <h4 class="fc-lbl">Against the reference</h4>
                <div class="fc-pair">
                  <figure>
                    <img :src="pictureThumbnailUrl(comparison.reference)" alt="" />
                    <figcaption>Cluster {{ comparison.referenceN }}, centre</figcaption>
                  </figure>
                  <figure>
                    <img :src="pictureThumbnailUrl(current.members[0].picture_id)" alt="" />
                    <figcaption>Cluster {{ selected + 1 }}, centre</figcaption>
                  </figure>
                </div>
                <span class="fc-num">{{ fixed(comparison.likeness) }}<small> alike</small></span>
                <p>{{ comparison.words }}</p>
                <p>{{ comparison.consequence }}</p>
              </div>
              <p v-else-if="!data.reference_cluster" class="fc-note">
                Nobody is {{ name }}'s reference yet. Picking a cluster makes its
                faces the ones {{ name }} is measured against, in sheets and
                training sets.
              </p>

              <div class="fc-dist">
                <h4 class="fc-lbl">Likeness to the centre</h4>
                <div class="fc-gradwrap" :style="{ '--fc-cut': `${cutPercent}%` }">
                  <div class="fc-grad" :style="{ background: gradient }" />
                  <div class="fc-grad-out" aria-hidden="true" />
                </div>
                <div class="fc-axis" aria-hidden="true">
                  <span>{{ fixed(axis.min, 1) }}</span><span>{{ fixed(axis.max, 1) }}</span>
                </div>
                <label class="fc-floor" :for="`${uid}-cut`"
                  >Accept down to <output>{{ fixed(cut) }}</output></label
                >
                <input
                  :id="`${uid}-cut`"
                  class="fc-slider"
                  type="range"
                  :min="axis.min"
                  :max="axis.max"
                  step="0.01"
                  :value="cut"
                  :style="{ '--fc-cut': `${cutPercent}%` }"
                  :aria-valuetext="`${fixed(cut)}, ${kept.length} of ${current.size} faces`"
                  data-testid="fc-cut"
                  @input="setCut(Number($event.target.value))"
                  @keydown.enter.prevent
                />
                <p>
                  <b>{{ kept.length }}</b> of {{ current.size }} faces count as
                  {{ name }}. Brighter where more faces sit; median
                  <b>{{ fixed(median) }}</b>. This is likeness to this cluster's
                  centre, not the training set's minimum likeness.
                </p>
                <h4 class="fc-lbl">Left out · {{ leftOut.length }}</h4>
                <div v-if="leftOut.length" class="fc-rim">
                  <figure v-for="m in leftOut.slice(0, 4)" :key="m.picture_id">
                    <img :src="pictureThumbnailUrl(m.picture_id)" alt="" />
                    <figcaption>
                      {{ fixed(m.centrality) }}<template v-if="m.score"> · {{ m.score }}★</template>
                    </figcaption>
                  </figure>
                  <span v-if="leftOut.length > 4" class="fc-more">+{{ leftOut.length - 4 }}</span>
                </div>
                <p v-else class="fc-note">Nothing left out.</p>
              </div>
            </aside>
          </div>
          <p class="fc-note fc-scale">
            Size: four bands of how typical a face is. Click, wheel up or Z on a
            face to zoom.
          </p>
        </section>
      </div>
    </div>

    <ZoomPopout
      :open="zoomedId != null"
      :src="zoomedFace ? faceUrl(zoomedFace) : ''"
      :alt="zoomedFace ? faceName(zoomRank) : ''"
      :caption="zoomedFace ? zoomCaption : ''"
      :has-prev="hasPrev"
      :has-next="hasNext"
      :entry="zoomEntry"
      @close="closeZoom"
      @prev="stepZoom(-1)"
      @next="stepZoom(1)"
    >
      <template #aside>
        <div class="fc-zoomref">
          <span class="fc-lbl">Centre of Cluster {{ selected + 1 }}</span>
          <img :src="pictureThumbnailUrl(current.members[0].picture_id)" alt="" />
          <span class="fc-note">{{ faceName(0) }}</span>
          <template v-if="comparison">
            <span class="fc-lbl">Centre of the reference</span>
            <img :src="pictureThumbnailUrl(comparison.reference)" alt="" />
          </template>
        </div>
      </template>
    </ZoomPopout>
  </AppDialog>
</template>

<script setup>
/**
 * "Faces and reference cluster…": a person's faces grouped by identity, and
 * the one group the owner picks as who the person is (the Cluster Inspector).
 *
 * The rail lists the clusters, reference first. The pane shows one cluster
 * centre-out, faces sized by how typical they are (`utils/clusterLayout`),
 * each with its likeness to the cluster's centre. The "Accept down to" slider
 * trims the rim before saving; what is saved is a frozen list of picture ids
 * (`PUT /characters/{id}/reference-cluster`). Any face opens in the zoom
 * pop-out, beside the cluster's centre face.
 */
import { computed, nextTick, ref, useId, watch } from "vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import Segmented from "../widgets/Segmented.vue";
import ZoomPopout from "../widgets/ZoomPopout.vue";
import { getFaceClusters, setReferenceCluster } from "../../api/characters";
import { pictureThumbnailUrl } from "../../api/pictures";
import { API_BASE_URL, appendShareToken } from "../../utils/apiClient";
import { buildMediaUrl } from "../../utils/media";
import { errorDetail } from "../../utils/apiError";
import { layoutCluster } from "../../utils/clusterLayout.js";
import { useZoomPopout } from "../../composables/useZoomPopout.js";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** `{ id, name }` */
  character: { type: Object, default: null },
  returnFocusTo: { type: Object, default: null },
});

const emit = defineEmits(["close", "changed"]);

// The "same person" cut in three steps (services/face_clusters.py STRICTNESS).
const STRICTNESS = [
  { id: "loose", label: "Loose", threshold: 0.35 },
  { id: "normal", label: "Normal", threshold: 0.45 },
  { id: "strict", label: "Strict", threshold: 0.55 },
];
// The face sizes, most typical first: --face-thumb-xl, --space-9, -8, -7.
const STEPS = [96, 64, 48, 32];
const MAX_SHOWN = 60;

const uid = useId();
const strictness = ref("normal");
const data = ref(null);
const error = ref("");
const selected = ref(0);
const cuts = ref({}); // cluster index -> the slider's value
const saving = ref(false);
const focusRank = ref(0);
const fieldEl = ref(null);
let seq = 0;

const name = computed(() => props.character?.name || "This person");
const strictnessLabel = computed(
  () => STRICTNESS.find((s) => s.id === strictness.value).label,
);
const clusters = computed(() => data.value?.clusters || []);
const unclustered = computed(() => data.value?.unclustered || []);
const current = computed(() => clusters.value[selected.value] || null);
const shown = computed(() => current.value?.members.slice(0, MAX_SHOWN) || []);
const hidden = computed(() => (current.value?.size || 0) - shown.value.length);
const layout = computed(() => layoutCluster(shown.value, STEPS));
const saved = computed(() => new Set(data.value?.reference_cluster || []));

const subtitle = computed(() => {
  if (!data.value) return "";
  const n = clusters.value.length;
  return `${data.value.faces} pictures with a face · ${n} ${n === 1 ? "person" : "people"} at ${strictnessLabel.value}`;
});

function fixed(v, digits = 2) {
  return v == null ? "" : Number(v).toFixed(digits);
}

/** The likeness axis: tenths around the cluster's spread, at least 0.1 wide. */
const axis = computed(() => {
  const c = shown.value.map((m) => m.centrality);
  if (!c.length) return { min: 0, max: 1 };
  const min = Math.max(0, Math.floor(Math.min(...c) * 10) / 10);
  const max = Math.min(1, Math.max(min + 0.1, Math.ceil(Math.max(...c) * 10) / 10));
  return { min, max };
});

/** Where the slider starts: the lowest saved face of the reference cluster,
 * else every face counts. */
function defaultCut(c) {
  const ins = c.members.filter((m) => saved.value.has(m.picture_id));
  const counted = c.is_reference && ins.length ? ins : c.members;
  return Math.min(...counted.map((m) => m.centrality));
}

const cut = computed(() => cuts.value[selected.value] ?? (current.value ? defaultCut(current.value) : 0));
const cutPercent = computed(
  () => ((cut.value - axis.value.min) / (axis.value.max - axis.value.min)) * 100,
);

function isOut(rank) {
  return shown.value[rank].centrality < cut.value - 1e-9;
}

const kept = computed(() =>
  (current.value?.members || [])
    .filter((m) => m.centrality >= cut.value - 1e-9)
    .map((m) => m.picture_id),
);
// Most typical first, so the faces just under the cut lead.
const leftOut = computed(() =>
  (current.value?.members || []).filter((m) => m.centrality < cut.value - 1e-9),
);
const changed = computed(
  () =>
    kept.value.length !== saved.value.size ||
    kept.value.some((id) => !saved.value.has(id)),
);
const median = computed(() => {
  const c = (current.value?.members || []).map((m) => m.centrality).sort((a, b) => a - b);
  if (!c.length) return null;
  const mid = Math.floor(c.length / 2);
  return c.length % 2 ? c[mid] : (c[mid - 1] + c[mid]) / 2;
});

/** One gradient bar: brighter where more faces sit. */
const gradient = computed(() => {
  const bins = 12;
  const counts = new Array(bins).fill(0);
  const { min, max } = axis.value;
  for (const m of current.value?.members || []) {
    const at = Math.min(bins - 1, Math.max(0, Math.floor(((m.centrality - min) / (max - min)) * bins)));
    counts[at]++;
  }
  const peak = Math.max(1, ...counts);
  const stops = counts.map(
    (n, i) =>
      `rgba(var(--v-theme-tertiary), ${(0.12 + (0.88 * n) / peak).toFixed(2)}) ${(((i + 0.5) / bins) * 100).toFixed(1)}%`,
  );
  return `linear-gradient(90deg, ${stops.join(", ")})`;
});

const referenceIndex = computed(() => clusters.value.findIndex((c) => c.is_reference));

/** Against the reference: on any cluster but the reference, once one exists. */
const comparison = computed(() => {
  const c = current.value;
  const r = referenceIndex.value;
  if (!c || c.is_reference || r < 0 || c.similarity_to_reference == null) return null;
  const sim = c.similarity_to_reference;
  const here = STRICTNESS.find((s) => s.id === strictness.value);
  const joins = STRICTNESS.find((s) => sim >= s.threshold);
  const words =
    sim >= here.threshold
      ? `Above ${here.label} (${fixed(here.threshold)}), so this may be ${name.value} in other light or another angle.`
      : `Below ${here.label} (${fixed(here.threshold)}), so this counts as someone else.${
          joins ? ` It would join the reference at ${joins.label}.` : ""
        }`;
  const refSize = data.value.reference_cluster?.length || 0;
  return {
    reference: clusters.value[r].members[0].picture_id,
    referenceN: r + 1,
    likeness: sim,
    words,
    consequence: `Picking it moves the reference to these ${kept.value.length} pictures. The ${refSize} in Cluster ${r + 1} then rank as low likeness.`,
  };
});

function meterOf(c) {
  return c.is_reference ? 100 : Math.max(0, Math.round(c.similarity_to_reference * 100));
}

function rowName(c, i) {
  const bits = [`Cluster ${i + 1}, ${c.size} pictures`];
  if (c.is_reference) bits.push("the reference");
  if (c.from_sheets) bits.push("new, from sheets");
  return bits.join(", ");
}

function faceName(rank) {
  const m = shown.value[rank];
  if (!m) return "";
  const score = m.score ? ` · ${m.score}★` : "";
  return `Face ${rank + 1} of ${current.value.size} · ${fixed(m.centrality)} to the centre${score}`;
}

function select(i) {
  if (i === selected.value) return;
  selected.value = i;
  focusRank.value = 0;
}

function setCut(v) {
  cuts.value = { ...cuts.value, [selected.value]: v };
}

function setStrictness(id) {
  if (id === strictness.value) return;
  strictness.value = id;
  load();
}

// ── Zoom ───────────────────────────────────────────────────────────────────
const zoomList = computed(() => shown.value.map((m) => m.picture_id));
const { zoomedId, zoomEntry, zoomPos, hasPrev, hasNext, openZoom, onTileWheel, stepZoom, closeZoom } =
  useZoomPopout(zoomList, (id) => {
    const rank = zoomList.value.indexOf(id);
    if (rank >= 0) focusFace(rank);
  });
const zoomRank = computed(() => zoomPos.value);
const zoomedFace = computed(() => shown.value[zoomRank.value] || null);
const zoomCaption = computed(() => {
  const m = zoomedFace.value;
  const rank = zoomRank.value;
  const where = rank === 0 ? ", most typical" : rank === current.value.size - 1 ? ", least typical" : "";
  const role = current.value.is_reference ? " (reference)" : "";
  return `Cluster ${selected.value + 1}${role} · face ${rank + 1} of ${current.value.size}${where} · ${fixed(m.centrality)} to the centre · ${m.score ? `${m.score}★` : "unrated"}`;
});

function faceUrl(m) {
  if (!m.format) return pictureThumbnailUrl(m.picture_id);
  return appendShareToken(
    buildMediaUrl({ backendUrl: API_BASE_URL, image: { id: m.picture_id, format: m.format } }),
  );
}

function focusFace(rank) {
  focusRank.value = rank;
  nextTick(() => fieldEl.value?.querySelector(`[data-rank="${rank}"]`)?.focus());
}

/** One tab stop: arrows walk the faces from most to least typical. */
function onFieldKey(event) {
  const last = shown.value.length - 1;
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
  if (step) {
    event.preventDefault();
    focusFace(Math.min(last, Math.max(0, focusRank.value + step)));
  } else if (event.key === "Home" || event.key === "End") {
    event.preventDefault();
    focusFace(event.key === "Home" ? 0 : last);
  } else if ((event.key === "z" || event.key === "Z") && !event.ctrlKey && !event.metaKey) {
    event.preventDefault();
    openZoom(shown.value[focusRank.value].picture_id);
  }
}

// ── Load and save ──────────────────────────────────────────────────────────
async function load() {
  const id = props.character?.id;
  if (id == null) return;
  const mine = ++seq;
  error.value = "";
  const threshold = STRICTNESS.find((s) => s.id === strictness.value).threshold;
  try {
    const result = await getFaceClusters(id, threshold);
    if (mine !== seq) return;
    data.value = result;
    cuts.value = {};
    selected.value = 0; // the reference cluster, when there is one, comes first
    focusRank.value = 0;
  } catch (e) {
    if (mine !== seq) return;
    error.value = `Couldn't group ${name.value}'s faces. ${errorDetail(e) || "Please try again."}`;
  }
}

async function save(ids) {
  saving.value = true;
  error.value = "";
  try {
    await setReferenceCluster(props.character.id, ids);
    emit("changed");
    await load();
  } catch (e) {
    error.value = `Couldn't save the reference. ${errorDetail(e) || "Please try again."}`;
  } finally {
    saving.value = false;
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    strictness.value = "normal";
    data.value = null;
    load();
  },
  { immediate: true },
);

function close() {
  emit("close");
  const row = props.returnFocusTo;
  nextTick(() => {
    if (row?.isConnected) row.focus();
  });
}
</script>

<style scoped>
.fc {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  height: min(720px, 80vh);
}
.fc-pad {
  padding: var(--space-5) var(--space-6);
}
.fc-split {
  flex: 1;
  min-height: 0;
  display: flex;
}
.fc-lbl {
  margin: 0;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.fc-lbl--rail {
  padding: 0 var(--space-5) var(--space-2);
}
.fc-note {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.fc-notice {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
  margin: var(--space-4) var(--space-6);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-input-background));
  font-size: var(--text-xs);
}

/* ── The rail ─────────────────────────────────────────────────────────── */
.fc-rail {
  width: 304px;
  flex: none;
  display: flex;
  flex-direction: column;
  padding: var(--space-4) 0;
  border-right: 1px solid rgb(var(--v-theme-divider));
  min-height: 0;
}
.fc-rows {
  overflow-y: auto;
  border: 1px solid transparent;
}
.fc-rows:focus-visible {
  outline: none;
  border-color: var(--focus-stroke);
  box-shadow: var(--focus-glow);
}
.fc-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-5);
  cursor: pointer;
}
.fc-row:hover {
  background: var(--hover-wash);
}
.fc-row[aria-selected="true"] {
  background: var(--active-wash);
}
.fc-row[aria-selected="true"]:hover {
  background:
    linear-gradient(var(--hover-wash), var(--hover-wash)),
    linear-gradient(var(--active-wash), var(--active-wash));
}
.fc-row[aria-selected="true"]::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--active-bar);
}
.fc-row__face {
  width: var(--space-8);
  height: var(--space-8);
  flex: none;
  object-fit: cover;
  border-radius: var(--radius-md);
}
.fc-row__text {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}
.fc-row__name {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-weight: var(--weight-semibold);
}
.fc-row__meta {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.fc-meter {
  display: block;
  width: 120px;
  height: var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--track-trough);
  overflow: hidden;
}
.fc-meter i {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-tertiary));
}
.fc-rail__foot {
  margin-top: auto;
  padding: var(--space-4) var(--space-5) 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.fc-strip {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.fc-strip img {
  width: var(--space-6);
  height: var(--space-6);
  object-fit: cover;
  border-radius: var(--radius-sm);
}
.fc-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  height: var(--badge-size);
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
}
.fc-badge--ref {
  background: var(--active-bar);
  color: rgb(var(--v-theme-on-primary));
}
.fc-badge--new {
  color: rgb(var(--v-theme-tertiary));
  box-shadow: inset 0 0 0 1px rgb(var(--v-theme-tertiary));
}

/* ── The pane ─────────────────────────────────────────────────────────── */
.fc-pane {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  padding: var(--space-5) var(--space-6);
  min-height: 0;
}
.fc-head {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}
.fc-title {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}
.fc-meta {
  flex: 1;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.fc-meta b {
  color: rgb(var(--v-theme-on-surface));
}
.fc-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: var(--space-7);
}
.fc-fieldwrap {
  flex: 1;
  min-width: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: safe center;
  gap: var(--space-3);
}
.fc-field {
  position: relative;
  flex: none;
}
.fc-face {
  position: absolute;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  overflow: hidden;
  background: rgb(var(--v-theme-input-background));
  cursor: zoom-in;
  transition:
    opacity var(--dur-2) var(--ease-standard),
    transform var(--dur-2) var(--ease-standard),
    filter var(--dur-2) var(--ease-standard);
}
.fc-face img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.fc-face:focus-visible {
  outline: none;
  border-color: var(--focus-stroke);
  box-shadow:
    inset 0 0 0 var(--focus-width) var(--focus-stroke),
    var(--focus-glow);
  z-index: var(--z-raised);
}
.fc-face--out {
  opacity: var(--opacity-disabled);
  transform: scale(0.82);
  filter: grayscale(1);
}
.fc-chip {
  position: absolute;
  height: var(--badge-size);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--scrim-photo-strong);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  line-height: var(--badge-size);
  font-variant-numeric: tabular-nums;
  pointer-events: none;
}
.fc-chip--lk {
  left: var(--space-1);
  bottom: var(--space-1);
}
.fc-chip--sc {
  right: var(--space-1);
  top: var(--space-1);
}
.fc-side {
  width: 264px;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  overflow-y: auto;
}
.fc-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}
.fc-card p,
.fc-dist p {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.fc-dist p b {
  color: rgb(var(--v-theme-on-surface));
}
.fc-pair {
  display: flex;
  gap: var(--space-4);
}
.fc-pair figure,
.fc-rim figure {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-variant-numeric: tabular-nums;
}
.fc-pair img {
  width: var(--face-thumb-xl);
  height: var(--face-thumb-xl);
  object-fit: cover;
  border-radius: var(--radius-md);
}
.fc-num {
  font-size: var(--text-2xl);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}
.fc-num small {
  font-size: var(--text-sm);
  font-weight: var(--weight-regular);
}
.fc-dist {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.fc-gradwrap {
  position: relative;
}
.fc-grad {
  height: var(--space-6);
  border-radius: var(--radius-sm);
}
.fc-grad-out {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: var(--fc-cut);
  border-radius: var(--radius-sm) 0 0 var(--radius-sm);
  background: var(--scrim-photo-strong);
  transition: width var(--dur-2) var(--ease-standard);
}
.fc-axis {
  display: flex;
  justify-content: space-between;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-variant-numeric: tabular-nums;
}
.fc-floor {
  display: flex;
  justify-content: space-between;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.fc-floor output {
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-surface));
  font-variant-numeric: tabular-nums;
}
.fc-slider {
  appearance: none;
  -webkit-appearance: none;
  width: 100%;
  height: var(--space-6);
  margin: 0;
  background: transparent;
  cursor: pointer;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
}
.fc-slider::-webkit-slider-runnable-track {
  height: var(--space-2);
  border-radius: var(--radius-pill);
  background: linear-gradient(
    90deg,
    var(--track-trough) var(--fc-cut),
    rgb(var(--v-theme-primary)) var(--fc-cut)
  );
}
.fc-slider::-moz-range-track {
  height: var(--space-2);
  border-radius: var(--radius-pill);
  background: linear-gradient(
    90deg,
    var(--track-trough) var(--fc-cut),
    rgb(var(--v-theme-primary)) var(--fc-cut)
  );
}
.fc-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: var(--space-5);
  height: var(--space-5);
  margin-top: calc((var(--space-2) - var(--space-5)) / 2);
  border-radius: 50%;
  background: rgb(var(--v-theme-primary));
  border: 2px solid rgb(var(--v-theme-on-primary));
}
.fc-slider::-moz-range-thumb {
  width: var(--space-4);
  height: var(--space-4);
  border-radius: 50%;
  background: rgb(var(--v-theme-primary));
  border: 2px solid rgb(var(--v-theme-on-primary));
}
.fc-slider:hover::-webkit-slider-thumb {
  box-shadow: 0 0 0 5px var(--hover-wash);
}
.fc-slider:focus-visible {
  outline: none;
  border-color: var(--focus-stroke);
  box-shadow: var(--focus-glow);
}
.fc-rim {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.fc-rim img {
  width: var(--space-8);
  height: var(--space-8);
  object-fit: cover;
  border-radius: var(--radius-md);
}
.fc-more {
  display: grid;
  place-items: center;
  width: var(--space-8);
  height: var(--space-8);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
  font-size: var(--text-xs);
}
.fc-zoomref {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.fc-zoomref img {
  width: var(--face-thumb-xl);
  height: var(--face-thumb-xl);
  object-fit: cover;
  border-radius: var(--radius-md);
}

@media (prefers-reduced-motion: reduce) {
  .fc-face,
  .fc-grad-out {
    transition: none;
  }
}
</style>
