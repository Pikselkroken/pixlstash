<template>
  <AppDialog
    :open="open"
    :title="`${name}'s faces`"
    :subtitle="subtitle"
    size="2xl"
    :pad-body="false"
    :persistent="saving || zoomedId != null || setName != null"
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
            aria-keyshortcuts="Z"
            data-testid="fc-rail"
            @keydown.down.prevent="select(Math.min(selected + 1, clusters.length - 1))"
            @keydown.up.prevent="select(Math.max(selected - 1, 0))"
            @keydown="onRailKey"
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
                data-testid="fc-row-face"
                @dblclick="zoomIn('clusters', c.members[0].picture_id)"
                @wheel="zoomIn('clusters', c.members[0].picture_id, $event)"
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
                data-testid="fc-strip-face"
                @dblclick="zoomIn('unclustered', u.picture_id)"
                @wheel="zoomIn('unclustered', u.picture_id, $event)"
              />
            </span>
          </div>
        </div>

        <section class="fc-pane" :aria-label="paneName">
          <div class="fc-head">
            <h3 v-if="centred" class="fc-title" data-testid="fc-centred-title">
              Centred on face
              <img
                class="fc-row__face"
                :src="pictureThumbnailUrl(centred.picture_id)"
                alt=""
                @dblclick="zoomIn('field', centred.picture_id)"
              />
            </h3>
            <h3 v-else class="fc-title">
              Cluster {{ selected + 1 }}
              <span v-if="current.is_reference" class="fc-badge fc-badge--ref"
                ><v-icon size="12">mdi-check</v-icon>Reference</span
              >
            </h3>
            <span v-if="centred" class="fc-meta" data-testid="fc-count"
              ><b>{{ kept.length }}</b> of {{ name }}'s {{ current.size }} faces
              count</span
            >
            <span v-else class="fc-meta" data-testid="fc-count"
              ><b>{{ kept.length }}</b> of {{ current.size }} pictures count ·
              holds together at {{ fixed(current.cohesion) }}</span
            >
            <template v-if="centred">
              <AppButton
                variant="secondary"
                :disabled="saving"
                data-testid="fc-uncentre"
                @click="uncentre"
                >Back to Cluster {{ selected + 1 }}'s centre</AppButton
              >
              <AppButton
                variant="primary"
                :loading="saving"
                :disabled="!kept.length"
                data-testid="fc-save-centred"
                @click="save(kept)"
                >Make these {{ name }}'s reference</AppButton
              >
            </template>
            <template v-else-if="current.is_reference">
              <AppButton
                variant="secondary"
                :disabled="saving"
                data-testid="fc-clear"
                @click="save([])"
                >Clear</AppButton
              >
              <AppButton
                variant="primary"
                :loading="saving"
                :disabled="!changed || !kept.length"
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
            <AppButton
              variant="secondary"
              :disabled="saving || !kept.length"
              data-testid="fc-save-set"
              @click="openSetName"
              >Save as set…</AppButton
            >
          </div>

          <div class="fc-body">
            <div class="fc-fieldwrap">
              <div ref="stageEl" class="fc-stage">
              <div
                ref="fieldEl"
                class="fc-field"
                role="group"
                :aria-label="
                  centred
                    ? 'Faces most like the chosen face first'
                    : `Faces of cluster ${selected + 1}, most typical first`
                "
                :style="fieldStyle"
                data-testid="fc-field"
                @keydown="onFieldKey"
              >
                <button
                  v-for="(f, rank) in layout.items"
                  :key="f.picture_id"
                  type="button"
                  class="fc-face"
                  :class="{
                    'fc-face--out': isOut(rank),
                    'fc-face--on': rank === focusRank,
                  }"
                  :style="{
                    left: `${f.x}px`,
                    top: `${f.y}px`,
                    width: `${f.w}px`,
                    height: `${f.h}px`,
                  }"
                  :tabindex="rank === focusRank ? 0 : -1"
                  :aria-label="faceName(rank)"
                  :aria-pressed="rank === focusRank"
                  :aria-keyshortcuts="'Z Enter C'"
                  :data-rank="rank"
                  data-testid="fc-face"
                  @focus="focusRank = rank"
                  @click="focusFace(rank)"
                  @dblclick="zoomIn('field', f.picture_id)"
                  @wheel="zoomIn('field', f.picture_id, $event)"
                >
                  <img :src="pictureThumbnailUrl(f.picture_id)" alt="" loading="lazy" />
                  <span class="fc-chip fc-chip--lk">{{ fixed(shown[rank].centrality) }}</span>
                  <span v-if="shown[rank].score" class="fc-chip fc-chip--sc"
                    >{{ shown[rank].score }}★</span
                  >
                </button>
              </div>
              </div>
              <span v-if="hidden" class="fc-note"
                >+{{ hidden }} more, {{ centred ? "less alike" : "less typical" }}</span
              >
            </div>

            <aside class="fc-side">
              <div v-if="picked" class="fc-picked" data-testid="fc-picked">
                <img
                  :src="pictureThumbnailUrl(picked.picture_id)"
                  alt=""
                  @dblclick="zoomIn('field', picked.picture_id)"
                  @wheel="zoomIn('field', picked.picture_id, $event)"
                />
                <span>
                  <b>Face {{ focusRank + 1 }} of {{ current.size }}</b><br />
                  {{ fixed(picked.centrality) }} {{ toWhat }}<template
                    v-if="picked.score"
                  >
                    · {{ picked.score }}★</template
                  ><br />
                  <span class="fc-note">{{
                    isOut(focusRank) ? "Left out at this cut." : "Counts at this cut."
                  }}</span>
                  <button
                    type="button"
                    class="fc-link"
                    @click="zoomIn('field', picked.picture_id)"
                  >
                    Zoom (Z)
                  </button>
                  <button
                    v-if="picked.picture_id !== centred?.picture_id"
                    type="button"
                    class="fc-link"
                    :disabled="centring"
                    :aria-busy="centring"
                    data-testid="fc-centre"
                    @click="centreOn(picked.picture_id)"
                  >
                    Centre on this face (C)
                  </button>
                </span>
              </div>
              <div v-if="comparison" class="fc-card" data-testid="fc-compare">
                <h4 class="fc-lbl">Against the reference</h4>
                <div class="fc-pair">
                  <figure>
                    <img
                      :src="pictureThumbnailUrl(comparison.reference)"
                      alt=""
                      @dblclick="zoomIn('compare', comparison.reference)"
                      @wheel="zoomIn('compare', comparison.reference, $event)"
                    />
                    <figcaption>Cluster {{ comparison.referenceN }}, centre</figcaption>
                  </figure>
                  <figure>
                    <img
                      :src="pictureThumbnailUrl(current.members[0].picture_id)"
                      alt=""
                      @dblclick="zoomIn('compare', current.members[0].picture_id)"
                      @wheel="zoomIn('compare', current.members[0].picture_id, $event)"
                    />
                    <figcaption>Cluster {{ selected + 1 }}, centre</figcaption>
                  </figure>
                </div>
                <span class="fc-num">{{ fixed(comparison.likeness) }}<small> alike</small></span>
                <p>{{ comparison.words }}</p>
                <p>{{ comparison.consequence }}</p>
              </div>
              <p v-else-if="!centred && !data.reference_cluster" class="fc-note">
                Nobody is {{ name }}'s reference yet. Picking a cluster makes its
                faces the ones {{ name }} is measured against, in sheets and
                training sets.
              </p>

              <div class="fc-dist">
                <h4 class="fc-lbl">
                  {{ centred ? "Likeness to the chosen face" : "Likeness to the centre" }}
                </h4>
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
                  <b>{{ fixed(median) }}</b>. This is likeness to
                  {{ centred ? "the face you chose" : "this cluster's centre" }}, not
                  the training set's minimum likeness.
                </p>
                <h4 class="fc-lbl">Left out · {{ leftOut.length }}</h4>
                <div v-if="leftOut.length" class="fc-rim">
                  <figure v-for="m in leftOut.slice(0, 4)" :key="m.picture_id">
                    <img
                      :src="pictureThumbnailUrl(m.picture_id)"
                      alt=""
                      data-testid="fc-rim-face"
                      @dblclick="zoomIn('leftOut', m.picture_id)"
                      @wheel="zoomIn('leftOut', m.picture_id, $event)"
                    />
                    <figcaption>
                      {{ fixed(m.centrality) }}<template v-if="m.score"> · {{ m.score }}★</template>
                    </figcaption>
                  </figure>
                  <button
                    v-if="leftOut.length > 4"
                    type="button"
                    class="fc-more fc-more--zoom"
                    :aria-label="`Zoom the ${leftOut.length - 4} more left out`"
                    data-testid="fc-more"
                    @click="zoomIn('leftOut', leftOut[4].picture_id)"
                  >
                    +{{ leftOut.length - 4 }}
                  </button>
                </div>
                <p v-else class="fc-note">Nothing left out.</p>
              </div>
            </aside>
          </div>
          <p class="fc-note fc-scale">
            Size: four bands of how typical a face is. Click a face to see it
            here; double-click any face, Ctrl+wheel or Z to zoom; C or
            <i>Centre on this face</i> to rank every face against it.
          </p>
        </section>
      </div>
    </div>

    <ZoomPopout
      :open="zoomedId != null"
      :src="zoomedFace ? faceUrl(zoomedFace) : ''"
      :alt="zoomedFace ? zoomCaption : ''"
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
          <span class="fc-lbl">{{
            centred ? "The chosen face" : `Centre of Cluster ${selected + 1}`
          }}</span>
          <img :src="pictureThumbnailUrl(current.members[0].picture_id)" alt="" />
          <span class="fc-note">{{ faceName(0) }}</span>
          <template v-if="comparison">
            <span class="fc-lbl">Centre of the reference</span>
            <img :src="pictureThumbnailUrl(comparison.reference)" alt="" />
          </template>
        </div>
      </template>
    </ZoomPopout>

    <!-- Mounted per opening, so the name field takes focus every time. -->
    <AppDialog
      v-if="setName != null"
      open
      title="Save as set"
      :subtitle="`${setIds.length} pictures from ${centred ? 'the centred faces' : `Cluster ${selected + 1}`}`"
      size="sm"
      :persistent="savingSet"
      @close="setName = null"
      @accept="saveAsSet"
    >
      <AppInput
        v-model="setName"
        label="Set name"
        autofocus
        autoselect
        :error="setError"
        data-testid="fc-set-name"
      />
      <template #footer>
        <AppButton variant="secondary" :disabled="savingSet" @click="setName = null"
          >Cancel</AppButton
        >
        <AppButton
          variant="primary"
          :loading="savingSet"
          :disabled="!setName?.trim()"
          data-testid="fc-set-create"
          @click="saveAsSet"
          >Create set</AppButton
        >
      </template>
    </AppDialog>
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
 *
 * "Centre on this face" (or C) ranks every face of the person, not only the
 * cluster's, by likeness to the chosen face (`centre` on the same GET) and
 * shows that ranking as the pane, slider and saves included. Nothing is
 * re-clustered or stored; the rail, "Back to …'s centre" or a strictness
 * change returns to the cluster.
 */
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import Segmented from "../widgets/Segmented.vue";
import ZoomPopout from "../widgets/ZoomPopout.vue";
import { getFaceClusters, setReferenceCluster } from "../../api/characters";
import { pictureThumbnailUrl } from "../../api/pictures";
import { bulkAddPicturesToSet, createPictureSet } from "../../api/pictureSets";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useOperationStore } from "../../stores/useOperationStore";
import { getEntityProjectIds } from "../../utils/projectMembership";
import { API_BASE_URL, appendShareToken } from "../../utils/apiClient";
import { buildMediaUrl, displayedAspectRatio } from "../../utils/media";
import { errorDetail } from "../../utils/apiError";
import { STEP_LADDER, layoutCluster } from "../../utils/clusterLayout.js";
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
const stageEl = ref(null);
const box = ref({ width: 0, height: 0 });
let seq = 0;
// The centred ranking: `{ picture_id, members }`, or null for the cluster's own.
const centred = ref(null);
const centreCut = ref(null);
const centring = ref(false);
let centreSeq = 0; // apart from `seq`: a re-centre never cancels a load, nor the reverse

// The room the faces have: they are laid out to fit it, never to scroll.
let observer = null;
watch(stageEl, (el) => {
  observer?.disconnect();
  observer = null;
  if (!el || typeof ResizeObserver === "undefined") return;
  observer = new ResizeObserver(() => {
    box.value = { width: el.clientWidth, height: el.clientHeight };
  });
  observer.observe(el);
});
onBeforeUnmount(() => observer?.disconnect());

const name = computed(() => props.character?.name || "This person");
const strictnessLabel = computed(
  () => STRICTNESS.find((s) => s.id === strictness.value).label,
);
const clusters = computed(() => data.value?.clusters || []);
const unclustered = computed(() => data.value?.unclustered || []);
const current = computed(() =>
  centred.value
    ? { members: centred.value.members, size: centred.value.members.length, centred: true }
    : clusters.value[selected.value] || null,
);
const threshold = computed(() => STRICTNESS.find((s) => s.id === strictness.value).threshold);
const toWhat = computed(() => (centred.value ? "to the chosen face" : "to the centre"));
const paneName = computed(() =>
  centred.value ? "Faces ranked against the chosen face" : `Cluster ${selected.value + 1}`,
);
const shown = computed(() => current.value?.members.slice(0, MAX_SHOWN) || []);
const hidden = computed(() => (current.value?.size || 0) - shown.value.length);
const layout = computed(() =>
  layoutCluster(
    shown.value.map((m) => ({
      picture_id: m.picture_id,
      aspect: displayedAspectRatio(m),
    })),
    STEP_LADDER,
    box.value,
  ),
);
/** Even the smallest faces with no spacing overflow: shrink the whole field. */
const fieldStyle = computed(() => {
  const { width, height, fits } = layout.value;
  const { width: w, height: h } = box.value;
  const scale = !fits && w && h ? Math.min(1, w / width, h / height) : 1;
  return {
    width: `${width}px`,
    height: `${height}px`,
    transform: scale < 1 ? `scale(${scale})` : undefined,
  };
});
const picked = computed(() => shown.value[focusRank.value] || null);
const saved = computed(() => new Set(data.value?.reference_cluster || []));

const subtitle = computed(() => {
  if (!data.value) return "";
  const n = clusters.value.length;
  return `${data.value.faces} pictures with a face · ${n} ${n === 1 ? "person" : "people"} at ${strictnessLabel.value}`;
});

function fixed(v, digits = 2) {
  return v == null ? "" : Number(v).toFixed(digits);
}

/** The likeness axis: tenths around the whole cluster's spread (not only the
 * faces shown), at least 0.1 wide, so the slider reaches every face. */
const axis = computed(() => {
  const c = (current.value?.members || []).map((m) => m.centrality);
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

// Centred, the slider starts at the strictness: every face counting would
// count every other person too.
const cut = computed(() => {
  if (centred.value) {
    const { min, max } = axis.value;
    return centreCut.value ?? Math.min(max, Math.max(min, threshold.value));
  }
  return cuts.value[selected.value] ?? (current.value ? defaultCut(current.value) : 0);
});
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
  return `Face ${rank + 1} of ${current.value.size} · ${fixed(m.centrality)} ${toWhat.value}${score}`;
}

function select(i) {
  uncentre(); // before the early return: the selected row goes back too
  if (i === selected.value) return;
  selected.value = i;
  focusRank.value = 0;
}

function setCut(v) {
  if (centred.value) centreCut.value = v;
  else cuts.value = { ...cuts.value, [selected.value]: v };
}

/** Rank every face of the person by likeness to *pictureId*'s face. */
async function centreOn(pictureId) {
  const id = props.character?.id;
  if (id == null) return;
  const mine = ++centreSeq;
  centring.value = true;
  try {
    const result = await getFaceClusters(id, threshold.value, pictureId);
    if (mine !== centreSeq) return;
    // `data` is kept: the answer's clusters are the same ones.
    centred.value = { picture_id: pictureId, members: result.centred };
    centreCut.value = null;
    // Into the field only if focus was there; a side-panel button keeps it.
    if (fieldEl.value?.contains(document.activeElement)) focusFace(0);
    else focusRank.value = 0;
  } catch (e) {
    if (mine !== centreSeq) return;
    console.error("Couldn't re-centre face clusters", id, pictureId, e);
    // A notice, not `error`: that would hide the whole pane, the way back with it.
    useNoticeStore().error(`Couldn't re-centre on that face. ${errorDetail(e) || "Please try again."}`, {
      key: "face-cluster-centre",
    });
  } finally {
    if (mine === centreSeq) centring.value = false;
  }
}

function uncentre() {
  if (!centred.value && !centring.value) return;
  centreSeq++; // an answer still in flight no longer applies
  centring.value = false;
  centred.value = null;
  centreCut.value = null;
  focusRank.value = 0;
}

function setStrictness(id) {
  if (id === strictness.value) return;
  strictness.value = id;
  load();
}

// ── Zoom ───────────────────────────────────────────────────────────────────
// Every thumbnail opens the same pop-out, stepping through the list it sits
// in: the field, the rail's cluster centres, "Matched no one", "Left out", or
// the two centres being compared.
const zoomFrom = ref("field");
const zoomItems = computed(() => {
  switch (zoomFrom.value) {
    case "clusters":
      return clusters.value.map((c) => c.members[0]);
    case "unclustered":
      return unclustered.value;
    case "leftOut":
      return leftOut.value;
    case "compare":
      return comparison.value
        ? [clusters.value[referenceIndex.value].members[0], current.value.members[0]]
        : [];
    default:
      return shown.value;
  }
});
const zoomList = computed(() => zoomItems.value.map((m) => m.picture_id));
let zoomOpener = null;
const { zoomedId, zoomEntry, zoomPos, hasPrev, hasNext, openZoom, onTileWheel, stepZoom, closeZoom } =
  useZoomPopout(zoomList, (id) => {
    const rank = zoomFrom.value === "field" ? zoomList.value.indexOf(id) : -1;
    if (rank >= 0) focusFace(rank);
    // A sidebar picture takes no focus itself: back to the rail or the field.
    else if (zoomOpener?.isConnected && zoomOpener !== document.body) zoomOpener.focus();
    else focusFace(focusRank.value);
  });

/** Open *id* from the list *from*; with a wheel *event*, only on Ctrl+wheel up. */
function zoomIn(from, id, event = null) {
  if (zoomedId.value != null) return;
  if (event && !(event.ctrlKey || event.metaKey)) return; // a plain wheel scrolls
  zoomFrom.value = from;
  zoomOpener = document.activeElement;
  if (event) onTileWheel(event, id);
  else openZoom(id);
}

const zoomedFace = computed(() => zoomItems.value[zoomPos.value] || null);
const zoomCaption = computed(() => {
  const m = zoomedFace.value;
  if (!m) return "";
  const score = m.score ? `${m.score}★` : "unrated";
  if (zoomFrom.value === "unclustered") {
    return `Matched no one · face ${zoomPos.value + 1} of ${unclustered.value.length} · ${score}`;
  }
  const at = centred.value?.members.indexOf(m) ?? -1;
  if (at >= 0) {
    const n = centred.value.members.length;
    const where = at === 0 ? ", the chosen face" : at === n - 1 ? ", least alike" : "";
    return `Centred · face ${at + 1} of ${n}${where} · ${fixed(m.centrality)} to the chosen face · ${score}`;
  }
  const i = clusters.value.findIndex((c) => c.members.includes(m));
  const c = clusters.value[i];
  const rank = c.members.indexOf(m);
  const where = rank === 0 ? ", most typical" : rank === c.size - 1 ? ", least typical" : "";
  const role = c.is_reference ? " (reference)" : "";
  return `Cluster ${i + 1}${role} · face ${rank + 1} of ${c.size}${where} · ${fixed(m.centrality)} to the centre · ${score}`;
});

/** Z zooms the selected cluster's centre from the rail. */
function onRailKey(event) {
  if ((event.key !== "z" && event.key !== "Z") || event.ctrlKey || event.metaKey) return;
  event.preventDefault();
  zoomIn("clusters", clusters.value[selected.value].members[0].picture_id);
}

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
  } else if (
    (event.key === "z" || event.key === "Z" || event.key === "Enter") &&
    !event.ctrlKey &&
    !event.metaKey
  ) {
    event.preventDefault();
    zoomIn("field", shown.value[focusRank.value].picture_id);
  } else if ((event.key === "c" || event.key === "C") && !event.ctrlKey && !event.metaKey) {
    event.preventDefault();
    const m = shown.value[focusRank.value];
    if (m && m.picture_id !== centred.value?.picture_id) centreOn(m.picture_id);
  }
}

// ── Load and save ──────────────────────────────────────────────────────────
async function load() {
  const id = props.character?.id;
  if (id == null) return;
  const mine = ++seq;
  error.value = "";
  uncentre();
  try {
    const result = await getFaceClusters(id, threshold.value);
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

// ── Save as set ────────────────────────────────────────────────────────────
// The faces that count, as a picture set under the person: a dataset to export
// and train elsewhere. The ids are taken when the name is asked for.
const setName = ref(null); // null: the name dialog is shut
const setIds = ref([]);
const setError = ref("");
const savingSet = ref(false);

function openSetName() {
  setIds.value = kept.value;
  setError.value = "";
  setName.value = centred.value
    ? `${name.value}, like one face`
    : `${name.value}, cluster ${selected.value + 1}`;
}

async function saveAsSet() {
  const title = setName.value?.trim();
  if (!title || savingSet.value) return;
  savingSet.value = true;
  setError.value = "";
  // The stores are looked up here, not at setup: every host mounts this dialog.
  const entityLists = useEntityListsStore();
  const noticeStore = useNoticeStore();
  const id = props.character.id;
  // A set under a person joins the person's projects (as SideBar does).
  const projectIds = getEntityProjectIds(entityLists.characters.find((c) => c.id === id));
  let set;
  try {
    ({ picture_set: set } = await createPictureSet({
      name: title,
      parent_character_id: id,
      ...(projectIds.length ? { project_ids: projectIds } : {}),
    }));
  } catch (e) {
    console.error("Couldn't create a set from face cluster", id, title, e);
    setError.value = `Couldn't create the set. ${errorDetail(e) || "Please try again."}`;
    savingSet.value = false;
    return;
  }
  try {
    // One bulk call: one operation-log entry and one Undo. The server's count,
    // not ours: it skips deleted pictures and brings in stack-mates.
    const n = (await bulkAddPicturesToSet(set.id, setIds.value))?.added ?? setIds.value.length;
    noticeStore.success(`Created ${set.name}, added ${n} picture${n === 1 ? "" : "s"}.`, {
      key: "face-cluster-set",
    });
    useOperationStore().refresh();
  } catch (e) {
    console.error("Couldn't add face-cluster pictures to set", set.id, e);
    noticeStore.error(`Created ${set.name}, but it is empty: couldn't add the pictures. ${errorDetail(e)}`, {
      key: "face-cluster-set",
    });
  } finally {
    savingSet.value = false;
    setName.value = null;
    entityLists.invalidate(["sets"]);
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    strictness.value = "normal";
    data.value = null;
    setName.value = null;
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
  min-width: 0;
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
  min-width: 0;
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
/* The fixed-box thumbnails (rail, strip, picked, pair, rim, zoom aside) show
   the whole frame, letterboxed: a square crop cut a full-body picture down to
   its torso. The field's tiles are sized to the picture instead. */
.fc-row__face {
  width: var(--space-8);
  height: var(--space-8);
  flex: none;
  object-fit: contain;
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
  object-fit: contain;
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
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-4);
}
.fc-title {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0 auto 0 0;
  white-space: nowrap;
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}
/* Its own line under the title and the buttons, so long button labels
   never squeeze it into a column. */
.fc-meta {
  order: 1;
  flex-basis: 100%;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.fc-meta b {
  color: rgb(var(--v-theme-on-surface));
}
.fc-body {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  gap: var(--space-5);
}
.fc-fieldwrap {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
}
/* The faces are laid out to fit this box (clusterLayout), so it never
   scrolls; the field shrinks as a whole only past the smallest sizes. */
.fc-stage {
  flex: 1;
  align-self: stretch;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  display: grid;
  place-items: center;
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
.fc-face--on {
  border-color: var(--active-bar);
  box-shadow: inset 0 0 0 var(--focus-width) var(--active-bar);
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
.fc-picked {
  display: flex;
  gap: var(--space-4);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}
.fc-picked img {
  width: var(--space-9);
  height: var(--space-9);
  flex: none;
  object-fit: contain;
  border-radius: var(--radius-md);
}
.fc-link {
  display: block;
  padding: 0;
  margin-top: var(--space-1);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: none;
  font: inherit;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}
.fc-link:focus-visible {
  outline: none;
  border-color: var(--focus-stroke);
  box-shadow: var(--focus-glow);
}
/* Every sidebar thumbnail zooms like the field's faces. */
.fc-row__face,
.fc-strip img,
.fc-picked img,
.fc-pair img,
.fc-rim img,
.fc-more--zoom {
  cursor: zoom-in;
}
.fc-more--zoom {
  padding: 0;
  border: 1px solid transparent;
  font: inherit;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
}
.fc-more--zoom:focus-visible {
  outline: none;
  border-color: var(--focus-stroke);
  box-shadow: var(--focus-glow);
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
  object-fit: contain;
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
  object-fit: contain;
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
  object-fit: contain;
  border-radius: var(--radius-md);
}

@media (prefers-reduced-motion: reduce) {
  .fc-face,
  .fc-grad-out {
    transition: none;
  }
}
</style>
