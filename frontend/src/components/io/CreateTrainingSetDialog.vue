<template>
  <AppDialog
    :open="open"
    title="Create a training set"
    :subtitle="subtitle"
    size="xl"
    :pad-body="false"
    :persistent="busy"
    @close="close"
    @accept="onAccept"
  >
    <div class="cts-pane" data-testid="cts-picks">
      <p v-if="loadError" class="cts-notice cts-notice--bad" role="alert">
        <v-icon size="16">mdi-alert-circle-outline</v-icon>
        <span>{{ loadError }}</span>
      </p>
      <p v-else-if="!data" class="cts-note">Ranking the pictures…</p>

      <template v-if="data">
        <section
          v-for="g in visibleGroups"
          :key="g.key"
          class="cts-grp"
          :data-testid="`cts-group-${g.key}`"
        >
          <div class="cts-grp-h">
            <h3 class="cts-secl">{{ g.title }}</h3>
            <span class="cts-note">{{ g.note }}</span>
          </div>
          <p
            v-if="!g.reachable"
            class="cts-notice"
            :data-testid="`cts-none-${g.key}`"
          >
            <v-icon size="16">mdi-information-outline</v-icon>
            <span>{{ g.noneText }}</span>
          </p>
          <template v-else>
            <div class="cts-slw">
              <div class="cts-sl">
                <div class="cts-range">
                  <input
                    :id="`${uid}-${g.key}`"
                    class="cts-range__input"
                    type="range"
                    :min="g.min"
                    :max="g.trackMax"
                    step="1"
                    :value="g.size"
                    :aria-label="`${g.title} in the set`"
                    :aria-valuetext="`${g.size} of ${g.reachable}`"
                    :data-testid="`cts-slider-${g.key}`"
                    @input="onSlide(g.key, $event)"
                  />
                  <span
                    v-if="g.capPercent > 0"
                    class="cts-range__cap"
                    :style="{ width: `${g.capPercent}%` }"
                    aria-hidden="true"
                  />
                </div>
                <span class="cts-val">{{ g.size }}</span>
                <span class="cts-note">of {{ g.passing }}</span>
              </div>
              <div v-if="g.mark" class="cts-marks" aria-hidden="true">
                <span
                  class="cts-mk"
                  :style="{ left: `${g.mark.percent}%` }"
                  >{{ g.mark.at }} · {{ g.mark.label }}</span
                >
              </div>
            </div>
            <p
              v-if="g.capped"
              class="cts-notice"
              :data-testid="`cts-capped-${g.key}`"
            >
              <v-icon size="16">mdi-information-outline</v-icon>
              <span>{{ g.cappedText }}</span>
            </p>
            <ul class="cts-sheet">
              <li
                v-for="p in g.shown"
                :key="p.picture_id"
                class="cts-pick"
                data-testid="cts-pick"
              >
                <div class="cts-tile">
                  <img
                    class="cts-tile__img"
                    :src="pictureThumbnailUrl(p.picture_id)"
                    :alt="chipLine(p)"
                    loading="lazy"
                  />
                  <span class="cts-tile__rk">{{ p.rank }}</span>
                  <span v-if="g.key === 'clips'" class="cts-tile__clip">
                    <v-icon size="12">mdi-video-outline</v-icon>
                  </span>
                </div>
                <div class="cts-cap" aria-hidden="true">
                  <span>{{ labelsLine(p) }}</span>
                  <span v-if="p.likeness != null" class="cts-cap__lk">{{
                    p.likeness
                  }}</span>
                </div>
              </li>
            </ul>
          </template>
        </section>
      </template>
    </div>

    <aside class="cts-pane cts-pane--side" data-testid="cts-coverage">
      <div class="cts-side-h">
        <h3 class="cts-secl">Coverage</h3>
        <Segmented
          v-if="data?.has_clips"
          v-model="coverageKey"
          aria-label="Coverage of"
          :options="[
            { id: 'stills', label: 'Pictures' },
            { id: 'clips', label: 'Clips' },
          ]"
        />
      </div>
      <template v-if="coverage">
        <div class="cts-legend">
          <u />picked <i />target for {{ coverage.size }}
          {{ coverage.unit }}
        </div>
        <div
          v-for="axis in coverage.axes"
          :key="axis.name"
          class="cts-axis"
          :class="`cts-axis--${axis.name}`"
        >
          <h4 class="cts-secl">{{ axis.title }}</h4>
          <div
            v-for="row in axis.rows"
            :key="row.label"
            class="cts-bar"
            :class="{ 'cts-bar--zero': !row.count }"
            :data-testid="`cts-bar-${row.label}`"
          >
            <span>{{ row.label }}</span>
            <span class="cts-bar__track">
              <span
                class="cts-bar__fill"
                :style="{ width: `${row.fillPercent}%` }"
              />
              <span
                class="cts-bar__tgt"
                :style="{ left: `${row.targetPercent}%` }"
              />
            </span>
            <span class="cts-bar__v">{{ row.count }}</span>
            <span class="cts-bar__of" :class="{ 'cts-bar__of--short': row.short }"
              ><v-icon v-if="!row.short" size="12">mdi-check</v-icon
              >{{ row.status }}</span
            >
          </div>
        </div>

        <div class="cts-gapbox">
          <template v-if="coverage.closable.length">
            <h4 class="cts-gk">The slider can close</h4>
            <div
              v-for="gap in coverage.closable"
              :key="gap.label"
              class="cts-g"
              data-testid="cts-gap-closable"
            >
              <v-icon size="16">mdi-arrow-right</v-icon>
              <span class="cts-g__t"
                >{{ capital(gap.label) }} {{ gap.short }} short · at
                {{ gap.at }}</span
              >
              <AppButton size="sm" @click="setSize(coverageKey, gap.at)">
                Show {{ gap.at }}
              </AppButton>
            </div>
          </template>
          <template v-if="coverage.outside.length">
            <h4 class="cts-gk">Past the top 50</h4>
            <div
              v-for="gap in coverage.outside"
              :key="gap.label"
              class="cts-g"
              data-testid="cts-gap-outside"
            >
              <v-icon size="16">mdi-dots-horizontal</v-icon>
              <span class="cts-g__t"
                >{{ capital(gap.label) }}: {{ gap.available }} in the pool,
                {{ gap.inTop ? `only ${gap.inTop}` : "none" }} in the top
                {{ gap.top }}</span
              >
            </div>
          </template>
          <template v-if="coverage.missing.length">
            <h4 class="cts-gk">Not in the library</h4>
            <div
              v-for="gap in coverage.missing"
              :key="gap.label"
              class="cts-g"
              data-testid="cts-gap-missing"
            >
              <v-icon size="16">mdi-minus-circle-outline</v-icon>
              <span class="cts-g__t"
                >No {{ gap.label.replace(/ /g, "-") }} {{ coverage.unit }} of
                {{ characterName }}</span
              >
            </div>
          </template>
        </div>
      </template>

      <div class="cts-gapbox">
        <h4 class="cts-gk">Set aside</h4>
        <div
          v-for="r in reasonRows"
          :key="r.key"
          class="cts-ex"
          :data-testid="`cts-reason-${r.key}`"
        >
          <span class="cts-ex__t">
            <span>{{ r.title }}</span>
            <small>{{ r.hint }}</small>
          </span>
          <span class="cts-badge">{{ r.count }}</span>
          <v-switch
            :model-value="setAside[r.key]"
            color="primary"
            density="compact"
            hide-details
            :aria-label="`Set aside: ${r.title}`"
            :disabled="busy"
            :data-testid="`cts-switch-${r.key}`"
            @update:model-value="(v) => toggle(r.key, !!v)"
          />
        </div>
      </div>
    </aside>

    <template #footer>
      <span v-if="created" class="cts-lead" data-testid="cts-created">
        <v-icon size="16" class="cts-ok">mdi-check-circle-outline</v-icon>
        Created <b>{{ created.name }}</b> · {{ created.members }}
        {{ created.members === 1 ? "member" : "members" }}
      </span>
      <span v-else-if="data" class="cts-lead">
        New set <b>{{ data.set_name }}</b> under {{ characterName }}
      </span>
      <p v-if="createError" class="cts-bad" role="alert">{{ createError }}</p>
      <template v-if="created">
        <AppButton key-hint="esc" @click="close">Close</AppButton>
        <AppButton
          variant="primary"
          key-hint="enter"
          icon-left="school-outline"
          data-testid="cts-train"
          @click="train"
        >
          Train a LoRA on it
        </AppButton>
      </template>
      <template v-else>
        <AppButton key-hint="esc" :disabled="busy" @click="close">
          Cancel
        </AppButton>
        <AppButton
          variant="primary"
          key-hint="enter"
          :loading="busy"
          :disabled="!total"
          data-testid="cts-create"
          @click="create"
        >
          Create set of {{ total }}
        </AppButton>
      </template>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * "Create a training set…" on a character's sidebar menu.
 *
 * Ranks the character's stills and clips (GET /training/character_set_suggestion)
 * and takes a prefix of each ranking with a slider, so a bigger set only adds
 * the next pick. The coverage column measures the picks against a target per
 * framing and expression. "Create" makes an ordinary child set under the
 * character; the user edits it like any other and trains on it.
 */
import { computed, nextTick, ref, useId, watch } from "vue";
import { VIcon, VSwitch } from "vuetify/components";

import { pictureThumbnailUrl } from "../../api/pictures";
import {
  bulkAddPicturesToSet,
  createPictureSet,
  deletePictureSet,
} from "../../api/pictureSets";
import { fetchCharacterSetSuggestion } from "../../api/training";
import { errorDetail } from "../../utils/apiError";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import Segmented from "../widgets/Segmented.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** { id, name, project_ids } of the character. */
  character: { type: Object, default: null },
  /** The sidebar row to hand focus back to on close. */
  returnFocusTo: { type: Object, default: null },
});

const emit = defineEmits(["close", "created", "train"]);

const TRACK_MAX = 50;
const DEFAULT_SIZE = { stills: 15, clips: 5 };
const REASONS = [
  {
    key: "other_people",
    title: "Other people",
    hint: "Someone else in frame",
  },
  {
    key: "near_duplicates",
    title: "Near-duplicates",
    hint: "Weaker copies of one shot",
  },
  {
    key: "low_quality",
    title: "Below the quality floor",
    hint: "Scored 1–2, or low smart score",
  },
  { key: "low_likeness", title: "Below the likeness floor", hint: "" },
];
const AXIS_TITLES = { framing: "Framing", expression: "Expression" };

const uid = useId();
const data = ref(null);
const loadError = ref("");
const sizes = ref({ ...DEFAULT_SIZE });
// Every reason sets its pictures aside until the user switches it off.
const allSetAside = () => Object.fromEntries(REASONS.map((r) => [r.key, true]));
const setAside = ref(allSetAside());
const coverageKey = ref("stills");
const busy = ref(false);
const created = ref(null);
const createError = ref("");
let fetchSeq = 0;

const characterName = computed(
  () => data.value?.character_name || props.character?.name || "",
);

const subtitle = computed(() => {
  if (!data.value) return characterName.value;
  const s = data.value.stills.candidates;
  const parts = [characterName.value, `${s} ${s === 1 ? "picture" : "pictures"}`];
  if (data.value.has_clips) {
    const c = data.value.clips.candidates;
    parts.push(`${c} ${c === 1 ? "clip" : "clips"}`);
  }
  return parts.join(" · ");
});

function reachableOf(group) {
  return Math.min(group.passing, group.picks.length);
}

function clampSize(key, value) {
  const reachable = reachableOf(data.value[key]);
  return Math.max(Math.min(value, reachable), Math.min(minOf(key), reachable));
}

function minOf(key) {
  return key === "stills" ? 5 : 1;
}

/** How many of *label* a set of *size* should hold (the backend's rule). */
function target(axis, label, size) {
  return Math.max(1, Math.floor(data.value.targets[axis][label] * size + 0.5));
}

function countIn(picks, axis, label) {
  return picks.filter((p) => (p.buckets[axis] || []).includes(label)).length;
}

/** Every label short of its target at the group's size, sorted into kinds. */
function gapsOf(key) {
  const group = data.value[key];
  const size = sizes.value[key];
  const reachable = reachableOf(group);
  const top = group.picks.slice(0, size);
  const out = { rows: {}, closable: [], outside: [], missing: [] };
  for (const axis of Object.keys(data.value.targets)) {
    out.rows[axis] = [];
    for (const label of Object.keys(data.value.targets[axis])) {
      const count = countIn(top, axis, label);
      const want = target(axis, label, size);
      const short = Math.max(0, want - count);
      const available = group.available[axis]?.[label] ?? 0;
      out.rows[axis].push({ label, count, want, short, none: !available });
      if (!short) continue;
      if (!available) {
        out.missing.push({ label });
        continue;
      }
      let at = null;
      for (let k = size + 1; k <= reachable; k++) {
        if (countIn(group.picks.slice(0, k), axis, label) >= want) {
          at = k;
          break;
        }
      }
      if (at) out.closable.push({ label, short, at });
      else
        out.outside.push({
          label,
          available,
          inTop: countIn(group.picks, axis, label),
          top: group.picks.length,
        });
    }
  }
  out.closable.sort((a, b) => a.at - b.at);
  return out;
}

function wouldAddText(group, unit) {
  const extra = REASONS.filter((r) => setAside.value[r.key])
    .map((r) => [r.title, group.would_add[r.key] || 0])
    .filter(([, n]) => n > 0);
  if (!extra.length) return "";
  const [first, ...rest] = extra;
  return ` Turning off ${first[0]} would let ${first[1]} more ${unit} in${rest
    .map(([t, n]) => `, ${t} ${n} more`)
    .join("")}.`;
}

const visibleGroups = computed(() => {
  if (!data.value) return [];
  const keys = data.value.has_clips ? ["stills", "clips"] : ["stills"];
  return keys.map((key) => {
    const group = data.value[key];
    const reachable = reachableOf(group);
    const size = sizes.value[key];
    const unit = key === "stills" ? "pictures" : "clips";
    const min = Math.min(minOf(key), Math.max(reachable, 1));
    const trackMax = key === "stills" ? TRACK_MAX : Math.max(reachable, min);
    const span = Math.max(trackMax - min, 1);
    const firstGap = key === coverageKey.value ? gapsOf(key).closable[0] : null;
    return {
      key,
      title: key === "stills" ? "Pictures" : "Video clips",
      note:
        key === "stills"
          ? "Used by every model."
          : "Used only when you train a video model. Still-picture models skip them, so it is safe to include both.",
      passing: group.passing,
      reachable,
      size,
      min,
      trackMax,
      capPercent:
        key === "stills" ? ((trackMax - Math.max(reachable, min)) / span) * 100 : 0,
      capped: reachable < TRACK_MAX && size >= reachable && key === "stills",
      cappedText: `Only ${reachable} ${unit} pass.${wouldAddText(group, unit)}`,
      noneText: `0 ${unit} pass.${wouldAddText(group, unit)}`,
      mark: firstGap
        ? {
            at: firstGap.at,
            label: firstGap.label,
            percent: ((firstGap.at - min) / span) * 100,
          }
        : null,
      shown: group.picks.slice(0, size),
    };
  });
});

const coverage = computed(() => {
  if (!data.value) return null;
  const key = data.value.has_clips ? coverageKey.value : "stills";
  if (!reachableOf(data.value[key])) return null;
  const gaps = gapsOf(key);
  const axes = Object.entries(gaps.rows).map(([name, rows]) => {
    const scale = Math.max(1, ...rows.map((r) => Math.max(r.count, r.want)));
    return {
      name,
      title: AXIS_TITLES[name] || name,
      rows: rows.map((r) => ({
        ...r,
        fillPercent: (r.count / scale) * 100,
        targetPercent: (r.want / scale) * 100,
        status: !r.short ? String(r.want) : r.none ? "none" : `${r.short} short`,
      })),
    };
  });
  return {
    size: sizes.value[key],
    unit: key === "stills" ? "pictures" : "clips",
    axes,
    closable: gaps.closable,
    outside: gaps.outside,
    missing: gaps.missing,
  };
});

const reasonRows = computed(() =>
  REASONS.map((r) => ({
    ...r,
    hint:
      r.key === "low_likeness"
        ? `Face matches ${characterName.value} poorly`
        : r.hint,
    count: data.value
      ? (data.value.stills.excluded[r.key] || 0) +
        (data.value.has_clips ? data.value.clips.excluded[r.key] || 0 : 0)
      : "–",
  })),
);

const pickedIds = computed(() => {
  if (!data.value) return [];
  const ids = data.value.stills.picks
    .slice(0, sizes.value.stills)
    .map((p) => p.picture_id);
  if (data.value.has_clips) {
    ids.push(
      ...data.value.clips.picks
        .slice(0, sizes.value.clips)
        .map((p) => p.picture_id),
    );
  }
  return ids;
});

const total = computed(() => pickedIds.value.length);

function labelsLine(p) {
  return [...(p.buckets.framing || []), ...(p.buckets.expression || [])].join(
    " · ",
  );
}

function chipLine(p) {
  const labels = labelsLine(p);
  const likeness = p.likeness != null ? `likeness ${p.likeness}` : "";
  return [labels, likeness].filter(Boolean).join(" · ");
}

function capital(text) {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

async function load() {
  const seq = ++fetchSeq;
  loadError.value = "";
  const include = Object.fromEntries(
    REASONS.map((r) => [r.key, !setAside.value[r.key]]),
  );
  try {
    const result = await fetchCharacterSetSuggestion(props.character.id, include);
    if (seq !== fetchSeq) return;
    data.value = result;
    for (const key of ["stills", "clips"]) {
      sizes.value[key] = clampSize(key, sizes.value[key]);
    }
  } catch (e) {
    if (seq !== fetchSeq) return;
    loadError.value = `Couldn't rank the pictures. ${errorDetail(e) || "Please try again."}`;
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    data.value = null;
    created.value = null;
    createError.value = "";
    coverageKey.value = "stills";
    sizes.value = { ...DEFAULT_SIZE };
    setAside.value = allSetAside();
    if (props.character?.id != null) load();
  },
  { immediate: true },
);

function toggle(key, on) {
  setAside.value = { ...setAside.value, [key]: on };
  load();
}

function setSize(key, value) {
  sizes.value = { ...sizes.value, [key]: clampSize(key, value) };
}

function onSlide(key, event) {
  const value = Number(event.target.value);
  setSize(key, value);
  // The track runs past what passes (hatched); the thumb stops at the cap.
  if (sizes.value[key] !== value) event.target.value = String(sizes.value[key]);
}

async function create() {
  if (busy.value || created.value || !total.value) return;
  busy.value = true;
  createError.value = "";
  const name = data.value.set_name;
  let setId = null;
  try {
    const response = await createPictureSet({
      name,
      parent_character_id: props.character.id,
      ...(props.character.project_ids?.length
        ? { project_ids: props.character.project_ids }
        : {}),
    });
    setId = response.picture_set.id;
    const added = await bulkAddPicturesToSet(setId, pickedIds.value);
    created.value = { id: setId, name, members: added.added ?? total.value };
    emit("created", created.value);
  } catch (e) {
    // The set was made but holds nothing: remove it rather than leave an
    // empty set behind, then say what failed.
    if (setId != null) {
      try {
        await deletePictureSet(setId);
      } catch (cleanup) {
        console.error("Couldn't remove the empty training set", setId, cleanup);
      }
    }
    createError.value = `Couldn't create the set. ${errorDetail(e) || "Please try again."}`;
  } finally {
    busy.value = false;
  }
}

function onAccept() {
  if (created.value) train();
  else create();
}

function train() {
  const set = created.value;
  close();
  emit("train", { type: "set", id: set.id, name: set.name });
}

function close() {
  if (busy.value) return;
  emit("close");
  // Opened from a context menu, so Vuetify has no activator to hand focus
  // back to: the character's sidebar row takes it.
  const row = props.returnFocusTo;
  nextTick(() => {
    if (row?.isConnected) row.focus();
  });
}
</script>

<style scoped>
.cts-pane {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

/* The coverage column stays in view while the picks scroll. */
.cts-pane--side {
  flex: 0 0 280px;
  gap: var(--space-4);
  border-left: 1px solid rgb(var(--v-theme-divider));
}

.cts-grp {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.cts-grp-h {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
}

.cts-secl {
  margin: 0;
  white-space: nowrap;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-note {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-slw {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.cts-sl {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.cts-range {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
}

.cts-range__input {
  width: 100%;
  margin: 0;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}

/* The part of the track no position can reach, hatched so the scale stays
   5-50 between visits and the reason the thumb stops is visible. */
.cts-range__cap {
  position: absolute;
  right: 0;
  height: 4px;
  pointer-events: none;
  border-radius: 0 var(--radius-pill) var(--radius-pill) 0;
  background: repeating-linear-gradient(
    90deg,
    rgba(var(--v-theme-on-surface), 0.3) 0 3px,
    transparent 3px 6px
  );
}

.cts-val {
  min-width: 2ch;
  text-align: right;
  font-family: var(--font-mono);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.cts-marks {
  position: relative;
  height: 16px;
  margin-right: 84px;
}

.cts-mk {
  position: absolute;
  transform: translateX(-50%);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  white-space: nowrap;
  display: flex;
  flex-direction: column;
  align-items: center;
  line-height: 1;
}

.cts-mk::before {
  content: "";
  width: 1px;
  height: 5px;
  margin-bottom: 2px;
  background: currentColor;
}

.cts-sheet {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-4) var(--space-3);
}

.cts-pick {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.cts-tile {
  position: relative;
  aspect-ratio: 1 / 1;
  overflow: hidden;
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
}

.cts-tile__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.cts-tile__rk,
.cts-tile__clip {
  position: absolute;
  min-width: 18px;
  height: 18px;
  padding: 0 var(--space-2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
}

.cts-tile__rk {
  top: var(--space-2);
  left: var(--space-2);
}

.cts-tile__clip {
  bottom: var(--space-2);
  left: var(--space-2);
}

.cts-cap {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: var(--text-xs);
}

.cts-cap span:first-child {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cts-cap__lk {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-side-h {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.cts-legend {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-legend u {
  display: inline-block;
  width: 12px;
  height: 8px;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-tertiary));
}

.cts-legend i {
  display: inline-block;
  width: 2px;
  height: 10px;
  margin-left: var(--space-2);
  background: rgb(var(--v-theme-on-surface));
}

.cts-axis {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.cts-bar {
  display: grid;
  grid-template-columns: 76px 1fr 16px 52px;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-xs);
}

.cts-bar__track {
  position: relative;
  height: 18px;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.07);
}

/* One identity hue per chart: framing teal, expression raspberry. */
.cts-bar__fill {
  position: absolute;
  inset: 0 auto 0 0;
  background: rgb(var(--v-theme-tertiary));
}

.cts-axis--expression .cts-bar__fill {
  background: rgb(var(--v-theme-secondary));
}

.cts-bar__tgt {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
  background: rgb(var(--v-theme-on-surface));
}

.cts-bar--zero > span:first-child {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-bar--zero .cts-bar__track {
  background: transparent;
  box-shadow: inset 0 0 0 1px rgb(var(--v-theme-border));
}

.cts-bar__v {
  font-family: var(--font-mono);
  text-align: right;
}

.cts-bar__of {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-1);
  white-space: nowrap;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-bar__of--short {
  color: rgb(var(--v-theme-surface-warning));
}

.cts-gapbox {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.cts-gk {
  margin: 0 0 calc(-1 * var(--space-2));
  font-size: var(--text-2xs);
  font-weight: var(--weight-regular);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-g {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.cts-g .v-icon {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-g__t {
  flex: 1;
  min-width: 0;
}

.cts-ex {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.cts-ex__t {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.cts-ex__t small {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-ex .v-switch {
  flex: 0 0 auto;
}

.cts-badge {
  min-width: 18px;
  height: 18px;
  padding: 0 var(--space-2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-cancel-button));
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
}

.cts-notice {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-input-background));
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.cts-notice .v-icon {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-notice--bad .v-icon {
  color: rgb(var(--v-theme-surface-error));
}

.cts-lead {
  margin-right: auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-lead b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

.cts-ok {
  color: rgb(var(--v-theme-surface-success));
}

.cts-bad {
  margin: 0;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-surface-error));
}
</style>
