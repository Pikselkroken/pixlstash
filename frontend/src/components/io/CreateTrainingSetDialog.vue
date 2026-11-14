<template>
  <AppDialog
    :open="open"
    title="Create a training set"
    :subtitle="subtitle"
    size="2xl"
    :pad-body="false"
    :persistent="busy || !!sheetStep?.working || !!sheetStep?.zooming"
    @close="close"
    @accept="onAccept"
  >
    <div class="cts-frame">
    <nav v-if="stepped" class="cts-stp" aria-label="Steps" data-testid="cts-steps">
      <button
        type="button"
        class="cts-stp__s"
        :class="mode === 'generate' ? 'cts-stp__s--on' : 'cts-stp__s--done'"
        :aria-current="mode === 'generate' ? 'step' : undefined"
        :disabled="mode === 'generate' || busy || !!created"
        @click="mode = 'generate'"
      >
        <span class="cts-stp__n"
          ><v-icon v-if="mode !== 'generate'" size="12">mdi-check</v-icon
          ><template v-else>1</template></span
        >Sheets
      </button>
      <span class="cts-stp__sep" aria-hidden="true" />
      <span
        class="cts-stp__s"
        :class="{ 'cts-stp__s--on': mode === 'pick' }"
        :aria-current="mode === 'pick' ? 'step' : undefined"
      >
        <span class="cts-stp__n">2</span>Pick the set
      </span>
      <span v-if="sheetStep?.loraName" class="cts-stp__lora"
        ><v-icon size="12">mdi-puzzle-outline</v-icon>with
        {{ sheetStep.loraName }}</span
      >
    </nav>
    <CharacterSheetStep
      v-if="stepped && character"
      v-show="mode === 'generate'"
      ref="sheetStep"
      :character="character"
      :picture-count="data?.stills?.candidates ?? 0"
      @done="onGenerated"
    />
    <div v-show="mode === 'pick'" class="cts-split">
    <div class="cts-pane cts-pane--picks" data-testid="cts-picks">
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
            <span
              v-if="g.capped && g.reachable"
              class="cts-note cts-note--warn"
              :title="g.cappedText"
              :data-testid="`cts-capped-${g.key}`"
              >{{ g.cappedShort }}</span
            >
            <span v-else class="cts-note">{{ g.note }}</span>
            <Segmented
              v-if="g.key === 'stills' && generated.length"
              v-model="newOnly"
              class="cts-newonly"
              aria-label="Show"
              :options="[
                { id: false, label: 'All' },
                { id: true, label: 'New only' },
              ]"
              data-testid="cts-new-only"
              @update:model-value="onNewOnly"
            />
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
                <div class="cts-range cts-range--kept" :style="{ '--cts-at': g.at }">
                  <span class="cts-range__fill" aria-hidden="true" />
                  <span
                    v-if="g.capPercent > 0"
                    class="cts-range__cap"
                    :style="{ width: `${g.capPercent}%` }"
                    aria-hidden="true"
                  />
                  <input
                    :id="`${uid}-${g.key}`"
                    class="cts-range__input"
                    type="range"
                    :min="g.min"
                    :max="g.trackMax"
                    step="1"
                    :value="g.size"
                    :disabled="busy || !!created"
                    :aria-label="`${g.title} in the set`"
                    :aria-valuetext="`${g.size} of ${g.reachable}`"
                    :data-testid="`cts-slider-${g.key}`"
                    @input="onSlide(g.key, $event)"
                    @keydown.enter.prevent
                  />
                </div>
                <span class="cts-val">{{ g.size }}</span>
                <span class="cts-note">of {{ g.passing }}</span>
              </div>
            </div>
            <ul class="cts-sheet">
              <li
                v-for="p in tilesOf(g)"
                :key="p.picture_id"
                class="cts-pick"
                :class="{ 'cts-pick--off': p.off }"
                data-testid="cts-pick"
              >
                <button
                  type="button"
                  class="cts-pick__x"
                  :aria-label="
                    p.off
                      ? `Put pick ${p.rank} back in the set`
                      : `Leave pick ${p.rank} out of the set`
                  "
                  :aria-pressed="p.off ? 'true' : 'false'"
                  :disabled="busy || !!created"
                  data-testid="cts-pick-toggle"
                  @click="toggleOff(p.picture_id)"
                >
                  <v-icon size="14">{{ p.off ? "mdi-undo" : "mdi-close" }}</v-icon>
                </button>
                <div
                  class="cts-tile"
                  role="button"
                  tabindex="0"
                  :aria-label="`Preview pick ${p.rank}`"
                  @click="previewId = p.picture_id"
                  @keydown.enter.prevent.stop="previewId = p.picture_id"
                  @keydown.space.prevent="previewId = p.picture_id"
                >
                  <img
                    class="cts-tile__img"
                    :src="pictureThumbnailUrl(p.picture_id)"
                    :alt="chipLine(p)"
                    loading="lazy"
                  />
                  <span class="cts-tile__rk">{{ p.rank }}</span>
                  <span
                    v-if="generatedSet.has(p.picture_id)"
                    class="cts-tile__new"
                    data-testid="cts-new"
                    ><i aria-hidden="true" />new</span
                  >
                  <span v-if="g.key === 'clips'" class="cts-tile__clip">
                    <v-icon size="12">mdi-video-outline</v-icon>
                  </span>
                  <span v-if="p.likeness != null" class="cts-tile__lk">{{
                    p.likeness
                  }}</span>
                </div>
                <div class="cts-cap" aria-hidden="true">
                  <span
                    v-for="c in chipsOf(p)"
                    :key="c.label"
                    class="cts-chip"
                    :class="c.hue"
                    >{{ c.label }}</span
                  >
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
        <div v-if="coverage" class="cts-legend">
          <u />picked <i />target for {{ coverage.size }}
          {{ coverage.unit }}
        </div>
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
            :class="{ 'cts-bar--zero': !row.count, 'cts-bar--none': row.none }"
            :data-testid="`cts-bar-${row.label}`"
          >
            <span>{{ row.label }}</span>
            <span class="cts-bar__track">
              <span
                class="cts-bar__fill"
                :class="hueOf(axis.name, row.label)"
                :style="{ width: `${row.fillPercent}%` }"
              />
              <span
                class="cts-bar__tgt"
                :style="{ left: `${row.targetPercent}%` }"
              />
              <span
                v-if="row.none"
                class="cts-bar__none"
                :style="noneStyle(row)"
                >not in the library</span
              >
            </span>
            <span class="cts-bar__v">{{ row.count }}</span>
            <span class="cts-bar__of" :class="{ 'cts-bar__of--short': row.short }"
              ><v-icon v-if="!row.short" size="12">mdi-check</v-icon
              >{{ row.status }}</span
            >
          </div>
        </div>
      </template>

      <div class="cts-gapbox" data-testid="cts-choosing">
        <TrainingPickPreview
          :pick="previewPick"
          :locked="busy || !!created"
          @close="previewId = null"
          @toggle="toggleOff"
        />
        <h3 class="cts-secl">Choosing</h3>
        <div class="cts-two">
          <div
            v-for="f in floorRows"
            :key="f.key"
            class="cts-ctl"
            :data-testid="`cts-floor-${f.key}`"
          >
            <div class="cts-ctl__h">
              <label :for="`${uid}-${f.key}`">{{ f.title }}</label>
              <span class="cts-val">{{ f.shown }}</span>
            </div>
            <div
              class="cts-range cts-range--kept"
              :style="{ '--cts-kept': f.shade, '--cts-at': f.at }"
            >
              <span class="cts-range__fill" aria-hidden="true" />
              <input
                :id="`${uid}-${f.key}`"
                class="cts-range__input"
                type="range"
                :min="f.min"
                :max="f.max"
                :step="f.step"
                :value="choosing[f.key]"
                :disabled="busy || !!created"
                :aria-valuetext="`${f.shown}, ${f.count} set aside`"
                :data-testid="`cts-slider-${f.key}`"
                @input="onFloor(f, $event)"
                @change="load"
                @keydown.enter.prevent
              />
            </div>
            <small class="cts-note"><b>{{ f.count }}</b> set aside</small>
          </div>
        </div>
        <div
          v-for="v in varietyRows"
          :key="v.key"
          class="cts-ctl cts-var"
          :data-testid="`cts-variety-${v.key}`"
          @keydown.enter.prevent
        >
          <div class="cts-var__row">
            <span class="cts-var__l">{{ v.short }}</span>
            <Segmented
              full
              :model-value="choosing[v.key]"
              :aria-label="v.ariaLabel"
              :options="VARIETY_STEPS"
              @update:model-value="(id) => setVariety(v.key, id)"
            />
          </div>
          <small class="cts-note cts-var__hint">{{ v.hint }}</small>
        </div>
        <div class="cts-aside">
        <span class="cts-note">Set aside</span>
        <div class="cts-two cts-two--tight">
        <div
          v-for="r in reasonRows"
          :key="r.key"
          class="cts-ex"
          :data-testid="`cts-reason-${r.key}`"
          @keydown.enter.prevent
        >
          <span class="cts-ex__t">{{ r.title }}</span>
          <span class="cts-badge">{{ r.count }}</span>
          <v-switch
            :model-value="setAside[r.key]"
            color="primary"
            density="compact"
            hide-details
            :aria-label="`Set aside: ${r.title}`"
            :disabled="busy || loading || !!created"
            :data-testid="`cts-switch-${r.key}`"
            @update:model-value="(v) => toggle(r.key, !!v)"
          />
        </div>
        </div>
        </div>
      </div>
    </aside>
    </div>
    </div>

    <template v-if="mode === 'generate'" #footer>
      <span
        class="cts-lead cts-lead--one"
        :class="{ 'cts-lead--warn': sheetStep?.footer?.warn }"
        :title="sheetStep?.footer?.lead"
        :role="sheetStep?.footer?.warn ? 'status' : undefined"
        data-testid="cts-generate-lead"
        ><v-icon v-if="sheetStep?.footer?.warn" size="16"
          >mdi-alert-outline</v-icon
        >
        {{ sheetStep?.footer?.lead }}</span
      >
      <p v-if="sheetStep?.error" class="cts-bad" role="alert">
        {{ sheetStep.error }}
      </p>
      <AppButton
        v-if="sheetStep?.footer?.back"
        :disabled="!!sheetStep?.working"
        @click="sheetStep.footer.back.run()"
      >
        {{ sheetStep.footer.back.label }}
      </AppButton>
      <AppButton
        key-hint="esc"
        :disabled="!!sheetStep?.working"
        data-testid="cts-generate-cancel"
        @click="leaveGenerate"
      >
        {{ hasPictures ? "Back to the picker" : "Cancel" }}
      </AppButton>
      <AppButton
        v-if="sheetStep?.footer?.secondary"
        :disabled="!sheetStep.footer.secondary.enabled"
        @click="sheetStep.footer.secondary.run()"
      >
        {{ sheetStep.footer.secondary.label }}
      </AppButton>
      <AppButton
        variant="primary"
        key-hint="enter"
        :loading="!!sheetStep?.working"
        :disabled="!sheetStep?.footer?.primary?.enabled"
        data-testid="cts-generate-primary"
        @click="sheetStep.footer.primary.run()"
      >
        {{ sheetStep?.footer?.primary?.label || "Draw sheets" }}
      </AppButton>
    </template>
    <template v-else #footer>
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
        <AppButton
          icon-left="draw"
          :disabled="busy || !data"
          data-testid="cts-generate"
          @click="startGenerate"
        >
          {{ hasPictures ? "Generate more…" : "Generate pictures…" }}
        </AppButton>
        <AppButton key-hint="esc" :disabled="busy" @click="close">
          Cancel
        </AppButton>
        <AppButton
          variant="primary"
          key-hint="enter"
          :loading="busy"
          :disabled="!total || loading || !!loadError"
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
 * framing and expression; under it, Choosing sets the two floors, how much
 * each kind of variety matters and the two set-aside switches. A tile opens
 * its picture at full size; its corner button leaves it out of the set, and
 * the next pick in the ranking takes its place. "Create" makes an ordinary child set under the
 * character; the user edits it like any other and trains on it.
 *
 * "Generate pictures…" draws the set instead (CharacterSheetStep): character
 * sheets, one chosen and cut into six pictures, which then join this ranking
 * marked new. A character with no pictures opens straight on that step.
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
import CharacterSheetStep from "./CharacterSheetStep.vue";
import AppDialog from "../widgets/AppDialog.vue";
import Segmented from "../widgets/Segmented.vue";
import TrainingPickPreview from "./TrainingPickPreview.vue";

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
// The two yes-or-no reasons: a switch each.
const REASONS = [
  { key: "other_people", title: "Other people" },
  { key: "near_duplicates", title: "Near-duplicates" },
];
// What the dialog builds until a control is moved. The floors match the
// backend's defaults; "important" lets a missing framing beat any repeat.
const CHOOSING = {
  min_likeness: 30,
  min_score: 3,
  framing_variety: "important",
  expression_variety: "important",
};
// `reason` is the floor's key in the response's excluded and would_add.
const FLOORS = [
  {
    key: "min_likeness",
    reason: "low_likeness",
    title: "Minimum likeness",
    min: 0,
    max: 100,
    step: 1,
  },
  {
    key: "min_score",
    reason: "low_quality",
    title: "Minimum score",
    min: 1,
    max: 5,
    step: 0.5,
  },
];
const VARIETY_STEPS = [
  { id: "unimportant", label: "Unimportant" },
  { id: "somewhat", label: "Somewhat" },
  { id: "important", label: "Important" },
];
const VARIETIES = [
  {
    key: "framing_variety",
    title: "Different framings",
    short: "Framings",
    word: "framing",
  },
  {
    key: "expression_variety",
    title: "Different expressions",
    short: "Expressions",
    word: "expression",
  },
];
const AXIS_TITLES = { framing: "Framing", expression: "Expression" };

const uid = useId();
const data = ref(null);
const loadError = ref("");
const sizes = ref({ ...DEFAULT_SIZE });
// Every reason sets its pictures aside until the user switches it off.
const allSetAside = () => Object.fromEntries(REASONS.map((r) => [r.key, true]));
const setAside = ref(allSetAside());
const choosing = ref({ ...CHOOSING });
// The ranking as the server gave it. `data` is this without the picks the
// user left out by hand, so the next pick in the ranking takes each one's
// place and every count follows.
// ponytail: replacements come from the 50 ranked picks only; ask the server
// for a longer ranking if people leave out many.
const ranked = ref(null);
const removed = ref(new Set());
const previewId = ref(null);
const coverageKey = ref("stills");
const busy = ref(false);
// A request is in flight: the picks on screen are for the previous filters.
const loading = ref(false);
const created = ref(null);
const createError = ref("");
let fetchSeq = 0;
// "generate" draws sheets (step 1), "pick" is the ranking (step 2, or the
// whole dialog when nothing is generated).
const mode = ref("pick");
const stepped = ref(false);
const sheetStep = ref(null);
// Pictures made in this dialog, marked new in the ranking.
const generated = ref([]);
const generatedSet = computed(() => new Set(generated.value));
const newOnly = ref(false);

const characterName = computed(
  () => data.value?.character_name || props.character?.name || "",
);

const hasPictures = computed(
  () =>
    !!data.value &&
    (data.value.stills.candidates > 0 ||
      (data.value.has_clips && data.value.clips.candidates > 0)),
);

const subtitle = computed(() => {
  if (!data.value) return characterName.value;
  const s = data.value.stills.candidates;
  const fresh = generated.value.length;
  const parts = [
    characterName.value,
    s ? `${s} ${s === 1 ? "picture" : "pictures"}` : "no pictures yet",
  ];
  // Made here: a panel whose face was never found is made but not filed.
  if (fresh) parts.push(`${fresh} new`);
  if (data.value.has_clips) {
    const c = data.value.clips.candidates;
    parts.push(`${c} ${c === 1 ? "clip" : "clips"}`);
  }
  return parts.join(" · ");
});

function reachableOf(group) {
  return Math.min(group.passing, group.picks.length);
}

/**
 * The ranking of *key* as shown: with "New only", just the pictures made here,
 * re-ranked from 1 and counted afresh, so the slider, the coverage and the set
 * all read the same list.
 */
function groupOf(key) {
  const group = data.value[key];
  if (!newOnly.value || key !== "stills") return group;
  const picks = group.picks
    .filter((p) => generatedSet.value.has(p.picture_id))
    .map((p, index) => ({ ...p, rank: index + 1 }));
  const available = {};
  for (const [axis, labels] of Object.entries(data.value.targets)) {
    available[axis] = {};
    for (const label of Object.keys(labels)) {
      available[axis][label] = countIn(picks, axis, label);
    }
  }
  return { ...group, picks, passing: picks.length, available };
}

function clampSize(key, value) {
  const reachable = reachableOf(groupOf(key));
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

/** Each label's count against its target at the group's size, per axis. */
function coverageRows(key) {
  const group = groupOf(key);
  const size = sizes.value[key];
  const top = group.picks.slice(0, size);
  const out = {};
  for (const axis of Object.keys(data.value.targets)) {
    out[axis] = [];
    for (const label of Object.keys(data.value.targets[axis])) {
      const count = countIn(top, axis, label);
      const want = target(axis, label, size);
      const short = Math.max(0, want - count);
      const available = group.available[axis]?.[label] ?? 0;
      out[axis].push({ label, count, want, short, none: !available });
    }
  }
  return out;
}

function wouldAddText(group, unit) {
  const extra = [
    ...REASONS.filter((r) => setAside.value[r.key]).map((r) => [
      `Keeping ${r.title.toLowerCase()}`,
      r.key,
    ]),
    ...FLOORS.map((f) => [`No ${f.title.toLowerCase()}`, f.reason]),
  ]
    .map(([what, reason]) => [what, group.would_add[reason] || 0])
    .filter(([, n]) => n > 0);
  if (!extra.length) return "";
  const [first, ...rest] = extra;
  return ` ${first[0]} would let ${first[1]} more ${unit} in${rest
    .map(([t, n]) => `, ${t[0].toLowerCase()}${t.slice(1)} ${n} more`)
    .join("")}.`;
}

const visibleGroups = computed(() => {
  if (!data.value) return [];
  const keys = data.value.has_clips ? ["stills", "clips"] : ["stills"];
  return keys.map((key) => {
    const group = groupOf(key);
    const reachable = reachableOf(group);
    const size = sizes.value[key];
    const unit = key === "stills" ? "pictures" : "clips";
    const min = Math.min(minOf(key), Math.max(reachable, 1));
    const trackMax = key === "stills" ? TRACK_MAX : Math.max(reachable, min);
    const span = Math.max(trackMax - min, 1);
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
      at: (size - min) / span,
      capPercent:
        key === "stills" ? ((trackMax - Math.max(reachable, min)) / span) * 100 : 0,
      capped: reachable < TRACK_MAX && size >= reachable && key === "stills",
      // In the heading, where it moves nothing; the long form is its tooltip.
      cappedShort:
        group.passing > reachable
          ? `Only ${reachable} left: put one back for more`
          : `Only ${reachable} ${unit} pass`,
      cappedText:
        group.passing > reachable
          ? `Only ${reachable} of the ranked ${unit} are left: the rest were left out by hand. Put one back to go higher.`
          : `Only ${reachable} ${unit} pass.${wouldAddText(group, unit)}`,
      noneText: `0 ${unit} pass.${wouldAddText(group, unit)}`,
      shown: group.picks.slice(0, size),
    };
  });
});

const coverage = computed(() => {
  if (!data.value) return null;
  const key = data.value.has_clips ? coverageKey.value : "stills";
  if (!reachableOf(groupOf(key))) return null;
  const axes = Object.entries(coverageRows(key)).map(([name, rows]) => {
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
  };
});

function withoutRemoved(result) {
  const strip = (group) => {
    const picks = group.picks.filter((p) => !removed.value.has(p.picture_id));
    return {
      ...group,
      picks,
      passing: group.passing - (group.picks.length - picks.length),
    };
  };
  return { ...result, stills: strip(result.stills), clips: strip(result.clips) };
}

/** A group's tiles: its picks, and in their ranked place the ones left out. */
function tilesOf(g) {
  const off = (ranked.value?.[g.key]?.picks || [])
    .filter((p) => removed.value.has(p.picture_id))
    .map((p) => ({ ...p, off: true }));
  return off.length ? [...g.shown, ...off].sort((a, b) => a.rank - b.rank) : g.shown;
}

const previewPick = computed(() => {
  if (previewId.value == null || !ranked.value) return null;
  const p = [...ranked.value.stills.picks, ...ranked.value.clips.picks].find(
    (pick) => pick.picture_id === previewId.value,
  );
  return p
    ? { ...p, label: chipLine(p), off: removed.value.has(p.picture_id) }
    : null;
});

function toggleOff(id) {
  if (busy.value || created.value || !ranked.value) return;
  const next = new Set(removed.value);
  if (!next.delete(id)) next.add(id);
  removed.value = next;
  data.value = withoutRemoved(ranked.value);
  for (const key of ["stills", "clips"]) {
    sizes.value[key] = clampSize(key, sizes.value[key]);
  }
}

/** How many pictures and clips *reason* sets aside; a dash before the answer. */
function setAsideCount(reason) {
  if (!data.value) return "–";
  return (
    (data.value.stills.excluded[reason] || 0) +
    (data.value.has_clips ? data.value.clips.excluded[reason] || 0 : 0)
  );
}

const reasonRows = computed(() =>
  REASONS.map((r) => ({ ...r, count: setAsideCount(r.key) })),
);

const floorRows = computed(() =>
  FLOORS.map((f) => ({
    ...f,
    shown:
      f.key === "min_score"
        ? `★ ${choosing.value.min_score.toFixed(1)}`
        : String(choosing.value[f.key]),
    count: setAsideCount(f.reason),
    at: (choosing.value[f.key] - f.min) / (f.max - f.min),
    shade: keptShade(data.value?.kept?.[f.key]),
  })),
);

/**
 * A floor's track, shaded by how many pictures each position would leave
 * (`kept`, one count per step): light where all remain, black where none do.
 */
function keptShade(counts) {
  if (!counts?.[0]) return undefined;
  const last = counts.length - 1;
  // ponytail: at most ~25 stops; the likeness floor has 101 steps.
  const every = Math.ceil(counts.length / 25);
  const stops = [];
  for (let i = 0; i <= last; i += every) stops.push(i);
  if (stops.at(-1) !== last) stops.push(last);
  return `linear-gradient(90deg, ${stops
    .map(
      (i) =>
        `color-mix(in srgb, var(--cts-kept-all) ${Math.round((counts[i] / counts[0]) * 100)}%, var(--cts-kept-none)) ${Math.round((i / last) * 100)}%`,
    )
    .join(", ")})`;
}

const varietyRows = computed(() =>
  VARIETIES.map((v) => ({
    ...v,
    ariaLabel: `How important ${v.title.toLowerCase()} are`,
    hint: {
      unimportant: "the best pictures win",
      somewhat: "a gap beats a repeat up to 60% better",
      important: `a missing ${v.word} always wins`,
    }[choosing.value[v.key]],
  })),
);

const pickedIds = computed(() => {
  if (!data.value) return [];
  const ids = groupOf("stills")
    .picks.slice(0, sizes.value.stills)
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

/** The colour of *label* on *axis*: the same on its bar and on its chips. */
function hueOf(axis, label) {
  const at = Object.keys(data.value?.targets?.[axis] || {}).indexOf(label);
  return at < 0 ? "" : `cts-hue--${axis}-${at % 4}`;
}

/** A pick's chips: its framings, then its expressions. */
function chipsOf(p) {
  return ["framing", "expression"].flatMap((axis) =>
    (p.buckets[axis] || []).map((label) => ({ label, hue: hueOf(axis, label) })),
  );
}

/** "not in the library" sits beside the target mark, on whichever side has room. */
function noneStyle(row) {
  return row.targetPercent > 50
    ? { right: `calc(${100 - row.targetPercent}% + var(--space-3))` }
    : { left: `calc(${row.targetPercent}% + var(--space-3))` };
}

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

async function load() {
  const seq = ++fetchSeq;
  loadError.value = "";
  loading.value = true;
  const query = { ...choosing.value };
  for (const r of REASONS) query[`include_${r.key}`] = !setAside.value[r.key];
  try {
    const result = await fetchCharacterSetSuggestion(props.character.id, query);
    if (seq !== fetchSeq) return;
    ranked.value = result;
    data.value = withoutRemoved(result);
    for (const key of ["stills", "clips"]) {
      sizes.value[key] = clampSize(key, sizes.value[key]);
    }
    // A person with clips only: the coverage starts on what was picked.
    if (!reachableOf(result.stills) && reachableOf(result.clips)) {
      coverageKey.value = "clips";
    }
    // Nothing to pick from: drawing is the only way to a set.
    if (!hasPictures.value && !generated.value.length) startGenerate();
  } catch (e) {
    if (seq !== fetchSeq) return;
    loadError.value = `Couldn't rank the pictures. ${errorDetail(e) || "Please try again."}`;
  } finally {
    if (seq === fetchSeq) loading.value = false;
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    choosing.value = { ...CHOOSING };
    removed.value = new Set();
    previewId.value = null;
    data.value = null;
    created.value = null;
    createError.value = "";
    coverageKey.value = "stills";
    sizes.value = { ...DEFAULT_SIZE };
    setAside.value = allSetAside();
    mode.value = "pick";
    stepped.value = false;
    generated.value = [];
    newOnly.value = false;
    if (props.character?.id != null) load();
  },
  { immediate: true },
);

function toggle(key, on) {
  setAside.value = { ...setAside.value, [key]: on };
  load();
}

/** The highest a floor goes: past it, it alone sets every picture aside. */
function floorCap(f) {
  const limit = data.value?.limits?.[f.key] ?? f.max;
  return Math.max(f.min, Math.min(f.max, limit));
}

function onFloor(f, event) {
  const value = Math.min(Number(event.target.value), floorCap(f));
  choosing.value[f.key] = value;
  // The track keeps its whole scale (black where nothing is left); the thumb stops.
  if (String(value) !== event.target.value) event.target.value = String(value);
}

function setVariety(key, id) {
  if (busy.value || created.value) return;
  choosing.value = { ...choosing.value, [key]: id };
  load();
}

function setSize(key, value) {
  sizes.value = { ...sizes.value, [key]: clampSize(key, value) };
}

function onSlide(key, event) {
  const value = Number(event.target.value);
  setSize(key, value);
  // The track runs past what passes (greyed); the thumb stops at the cap.
  if (sizes.value[key] !== value) event.target.value = String(sizes.value[key]);
}

async function create() {
  if (busy.value || loading.value || loadError.value) return;
  if (created.value || !total.value) return;
  busy.value = true;
  createError.value = "";
  const name = data.value.set_name;
  // What the user submitted, taken before the first await.
  const ids = pickedIds.value;
  let setId = null;
  let why;
  try {
    const response = await createPictureSet({
      name,
      parent_character_id: props.character.id,
      ...(props.character.project_ids?.length
        ? { project_ids: props.character.project_ids }
        : {}),
    });
    setId = response.picture_set.id;
    const added = await bulkAddPicturesToSet(setId, ids);
    if (added.added === 0) {
      why = "None of the picked pictures could be added; they may have been deleted.";
    } else {
      created.value = { id: setId, name, members: added.added ?? ids.length };
      busy.value = false;
      emit("created", created.value);
      return;
    }
  } catch (e) {
    why = errorDetail(e) || "Please try again.";
  }
  // The set was made but holds nothing: remove it rather than leave an empty
  // set behind, then say what failed.
  if (setId != null) {
    try {
      await deletePictureSet(setId);
    } catch (cleanup) {
      console.error("Couldn't remove the empty training set", setId, cleanup);
    }
  }
  createError.value = `Couldn't create the set. ${why}`;
  busy.value = false;
}

function startGenerate() {
  stepped.value = true;
  mode.value = "generate";
}

function leaveGenerate() {
  if (hasPictures.value || generated.value.length) mode.value = "pick";
  else close();
}

function onGenerated(ids) {
  generated.value = [...new Set([...generated.value, ...ids])];
  mode.value = "pick";
  load();
}

function onNewOnly() {
  for (const key of ["stills", "clips"]) {
    sizes.value[key] = clampSize(key, sizes.value[key]);
  }
}

function onAccept() {
  if (mode.value === "generate") {
    const primary = sheetStep.value?.footer?.primary;
    if (primary?.enabled) primary.run();
    return;
  }
  if (created.value) train();
  else create();
}

function train() {
  const set = created.value;
  close();
  emit("train", { type: "set", id: set.id, name: set.name });
}

function close() {
  if (busy.value || sheetStep.value?.working) return;
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
.cts-frame {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  /* One height for every step and state, so the dialog never resizes as
     sheets arrive, notices show or sheets are ticked: the panes scroll
     instead. About 768px with the title and footer, less on a short window. */
  height: min(652px, calc(100vh - 160px));
  /* One colour per coverage bar and its chips, from four hues, each deep and
     light. The same eight in both themes: the light ones are mixed toward the
     dark-surface text colour and carry a dark label. */
  --cts-light: rgb(var(--v-theme-on-dark-surface));
  --cts-kept-all: rgb(var(--v-theme-on-dark-surface));
  --cts-kept-none: rgb(var(--v-theme-scrim));
}

.cts-hue--framing-0 {
  background: color-mix(in srgb, rgb(var(--v-theme-tertiary)) 55%, var(--cts-light));
  color: rgb(var(--v-theme-dark-surface));
}

.cts-hue--framing-1 {
  background: rgb(var(--v-theme-tertiary));
  color: rgb(var(--v-theme-on-tertiary));
}

.cts-hue--framing-2 {
  background: rgb(var(--v-theme-dark-surface-primary));
  color: rgb(var(--v-theme-dark-surface));
}

.cts-hue--framing-3 {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

.cts-hue--expression-0 {
  background: color-mix(in srgb, rgb(var(--v-theme-secondary)) 55%, var(--cts-light));
  color: rgb(var(--v-theme-dark-surface));
}

.cts-hue--expression-1 {
  background: rgb(var(--v-theme-secondary));
  color: rgb(var(--v-theme-on-secondary));
}

.cts-hue--expression-2 {
  background: color-mix(in srgb, rgb(var(--v-theme-quaternary)) 55%, var(--cts-light));
  color: rgb(var(--v-theme-dark-surface));
}

.cts-hue--expression-3 {
  background: rgb(var(--v-theme-quaternary));
  color: rgb(var(--v-theme-on-primary));
}

.cts-split {
  flex: 1;
  min-height: 0;
  display: flex;
}

/* The wizard's steps: a numbered row under the title. Not a DS component yet
   (the design draws it new); built from the selection tokens. */
.cts-stp {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-5);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-stp__s {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 0;
  border: 1px solid transparent;
  background: none;
  color: inherit;
  font: inherit;
  white-space: nowrap;
}

button.cts-stp__s:not(:disabled) {
  cursor: pointer;
}

.cts-stp__s--on {
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-medium);
}

.cts-stp__s--done {
  color: rgb(var(--v-theme-on-surface));
}

.cts-stp__n {
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px solid rgb(var(--v-theme-border));
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
}

.cts-stp__s--on .cts-stp__n {
  background: var(--active-wash);
  border-color: var(--active-bar);
}

.cts-stp__s--done .cts-stp__n {
  background: rgb(var(--v-theme-primary));
  border-color: transparent;
  color: rgb(var(--v-theme-on-primary));
}

.cts-stp__sep {
  flex: 1;
  min-width: 12px;
  max-width: 40px;
  height: 1px;
  background: rgb(var(--v-theme-border));
}

/* Names the LoRA in use: it identifies, it does not select, so neutral. */
.cts-stp__lora {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 20px;
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-cancel-button));
  font-size: var(--text-2xs);
  color: rgb(var(--v-theme-on-surface));
}

.cts-newonly {
  margin-left: auto;
}

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
  flex: 0 0 410px;
  gap: var(--space-4);
  border-left: 1px solid rgb(var(--v-theme-divider));
}

.cts-grp {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* Only the tiles scroll: a group's heading and count slider stay above them.
   Two groups (pictures and clips) share the height evenly. */
.cts-pane--picks {
  overflow: hidden;
}

.cts-pane--picks .cts-grp {
  flex: 1 1 0;
  min-height: 0;
}

.cts-pane--picks .cts-sheet {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  align-content: start;
  /* The scrollbar sits at the pane's edge, not inside its padding. */
  margin-right: calc(-1 * var(--space-5));
  padding-right: var(--space-5);
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

.cts-note--warn {
  color: rgb(var(--v-theme-surface-warning));
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

/* The part of the track no position can reach, one flat grey, so the scale
   stays 5-50 between visits and the reason the thumb stops is visible. It
   comes before the input, so the thumb is drawn over it. */
.cts-range__cap {
  position: absolute;
  right: 0;
  top: var(--space-2);
  bottom: var(--space-2);
  pointer-events: none;
  border-radius: 0 var(--radius-pill) var(--radius-pill) 0;
  background: var(--track-ring);
}

/* A drawn slider (the floors, and the count): the track is shaded by how many pictures each position
   would leave (--cts-kept), under a translucent fill up to the thumb. Not a
   DS slider variant yet; the native track is cleared and drawn here. */
.cts-range--kept {
  height: var(--space-5);
}

.cts-range--kept::before {
  content: "";
  position: absolute;
  inset: var(--space-2) 0;
  border-radius: var(--radius-pill);
  background: var(--cts-kept, var(--track-trough));
  box-shadow: inset 0 0 0 1px var(--track-ring);
}

/* Ends under the thumb's centre, which travels the track less its own width. */
.cts-range__fill {
  position: absolute;
  left: 0;
  top: var(--space-2);
  bottom: var(--space-2);
  width: calc(7px + (100% - 14px) * var(--cts-at, 0));
  border-radius: var(--radius-pill) 0 0 var(--radius-pill);
  background: rgba(var(--v-theme-primary), 0.4);
  pointer-events: none;
}

.cts-range--kept .cts-range__input {
  position: relative;
  height: var(--space-5);
  appearance: none;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
}

.cts-range--kept .cts-range__input::-webkit-slider-runnable-track {
  height: 14px;
  background: transparent;
}

.cts-range--kept .cts-range__input::-moz-range-track {
  background: transparent;
}

.cts-range--kept .cts-range__input::-webkit-slider-thumb {
  appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: rgb(var(--v-theme-primary));
  box-shadow: 0 0 0 2px rgb(var(--v-theme-surface));
}

.cts-range--kept .cts-range__input::-moz-range-thumb {
  width: 14px;
  height: 14px;
  border: none;
  border-radius: 50%;
  background: rgb(var(--v-theme-primary));
  box-shadow: 0 0 0 2px rgb(var(--v-theme-surface));
}

.cts-range--kept .cts-range__input:disabled {
  cursor: not-allowed;
}

.cts-range--kept:has(:disabled) {
  opacity: var(--opacity-disabled);
}

.cts-val {
  min-width: 2ch;
  text-align: right;
  font-family: var(--font-mono);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.cts-sheet {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  /* Four across at most, fewer in a narrow window: a pick can carry two
     expressions beside its framing, and three chips need the wider tile. */
  grid-template-columns: repeat(
    auto-fill,
    minmax(max(144px, calc((100% - 3 * var(--space-3)) / 4)), 1fr)
  );
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

.cts-tile[role="button"] {
  cursor: zoom-in;
}

/* Left out by hand: greyed, still in its ranked place so it can come back. */
.cts-pick--off .cts-tile,
.cts-pick--off .cts-cap {
  opacity: var(--opacity-disabled);
}

.cts-pick--off .cts-tile__img {
  filter: grayscale(1);
}

.cts-pick {
  position: relative;
}

.cts-pick__x {
  position: absolute;
  z-index: var(--z-raised);
  top: var(--space-2);
  right: var(--space-2);
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  padding: 0;
  display: grid;
  place-items: center;
  border: none;
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  cursor: pointer;
}

.cts-pick__x:hover:not(:disabled) {
  background: var(--scrim-photo-strong);
}

.cts-pick__x:disabled {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

.cts-tile__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.cts-tile__rk,
.cts-tile__lk,
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

.cts-tile__lk {
  bottom: var(--space-2);
  right: var(--space-2);
  font-family: var(--font-mono);
  font-weight: var(--weight-regular);
}

.cts-tile__new {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  height: 18px;
  padding: 0 var(--space-2);
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
}

.cts-tile__new i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--active-bar);
}

.cts-cap {
  display: flex;
  gap: var(--space-2);
  overflow: hidden;
}

.cts-chip {
  flex: none;
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  line-height: var(--space-5);
  white-space: nowrap;
}

.cts-side-h {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2) var(--space-3);
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
  background: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
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
  line-height: var(--space-5);
}

.cts-bar__track {
  position: relative;
  height: var(--space-5);
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.07);
}

/* Each bar takes its own colour (.cts-hue--*), shared with its chips. */
.cts-bar__fill {
  position: absolute;
  inset: 0 auto 0 0;
}

.cts-bar__none {
  position: absolute;
  top: 0;
  font-size: var(--text-2xs);
  white-space: nowrap;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cts-bar--none .cts-bar__tgt {
  opacity: 0.55;
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

.cts-ex {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.cts-ex__t {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
}

/* A variety control on one line: its short label beside the steps, the hint
   under them. */
.cts-var {
  gap: var(--space-1);
}

.cts-var__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.cts-var__l {
  flex: 0 0 88px;
}

/* Three long words in the width left: tighter sides than a segment's own. */
.cts-var :deep(.seg__opt) {
  padding: 0 var(--space-2);
}

.cts-var__hint {
  text-align: right;
  font-size: var(--text-2xs);
}

.cts-aside {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.cts-two--tight {
  gap: var(--space-4);
}

/* The two floors side by side, each with its own count under its slider. */
.cts-two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-5);
}

.cts-ctl {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
  font-size: var(--text-sm);
}

.cts-ctl__h {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 0 var(--space-3);
}

.cts-note b {
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-surface));
}

.cts-ex .v-switch {
  flex: 0 0 auto;
}

/* Vuetify's compact switch is still 40px tall: the row takes the small
   control height instead. */
.cts-ex :deep(.v-selection-control) {
  min-height: var(--control-h-sm);
}

.cts-ex :deep(.v-selection-control__wrapper) {
  height: var(--control-h-sm);
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

/* The sheet step's lead stays one line, so the footer keeps its height. */
.cts-lead--one {
  display: block;
  min-width: 0;
  flex: 1 1 auto;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cts-lead--warn {
  color: rgb(var(--v-theme-on-surface));
}

.cts-lead--warn .v-icon {
  color: rgb(var(--v-theme-surface-warning));
  vertical-align: -3px;
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
