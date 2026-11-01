<template>
  <AppDialog
    :open="store.trainDialogOpen"
    title="Train a LoRA"
    :subtitle="subtitle"
    size="lg"
    :persistent="store.submitting"
    @close="store.closeDialog()"
    @accept="onStart"
  >
    <!-- ai-toolkit not configured: Start is blocked, the way out is Settings. -->
    <div
      v-if="store.configLoaded && !store.aiToolkitConfigured"
      class="tld-notice tld-notice--warn"
      role="status"
    >
      <v-icon size="16">mdi-alert-outline</v-icon>
      <span>
        AI-toolkit isn't set up yet.
        <button type="button" class="tld-link" @click="goToSettings">
          Set up AI-toolkit first
        </button>
      </span>
    </div>
    <p v-if="store.datasetError" class="tld-notice tld-notice--bad" role="alert">
      <v-icon size="16">mdi-alert-circle-outline</v-icon>
      <span>{{ store.datasetError }}</span>
    </p>

    <!-- The Models workspace's door has no set or person in view, so it asks
         first (#2169): people, then sets, each with its picture count. -->
    <div v-if="store.pickable" class="tld-field" data-testid="tld-train-on">
      <FieldLabel :id="sourceLabelId">Train on</FieldLabel>
      <div
        role="radiogroup"
        class="tld-sources"
        :aria-labelledby="sourceLabelId"
        @keydown="onSourceKey"
      >
        <button
          v-for="o in sourceOptions"
          :key="o.id"
          type="button"
          role="radio"
          class="tld-source"
          :class="{ 'tld-opt--on': o.id === pickedSource }"
          :aria-checked="o.id === pickedSource ? 'true' : 'false'"
          :tabindex="o.id === sourceTabStop ? 0 : -1"
          :data-testid="`tld-source-${o.id}`"
          @click="pickSource(o)"
        >
          <img
            v-if="o.thumb && !broken.has(o.id)"
            class="tld-source__img"
            :class="{ 'tld-source__img--person': o.type === 'character' }"
            :src="o.thumb"
            alt=""
            loading="lazy"
            @error="broken.add(o.id)"
          />
          <span
            v-else
            class="tld-source__img tld-source__img--blank"
            :class="{ 'tld-source__img--person': o.type === 'character' }"
            aria-hidden="true"
          >
            <v-icon size="20">{{
              o.type === "character" ? "mdi-account-outline" : "mdi-image-album"
            }}</v-icon>
          </span>
          <span class="tld-source__text">
            <span class="tld-source__name">{{ o.name }}</span>
            <small>{{ o.detail }}</small>
          </span>
        </button>
        <p v-if="!sourceOptions.length" class="tld-hint">
          No people or sets to train on yet.
        </p>
      </div>
    </div>

    <!-- A person (or a person's reference set): the subject is the lead, not
         a question. -->
    <div v-if="character" class="tld-lead" data-testid="tld-lead">
      <img
        v-if="!broken.has(`character:${character.id}`)"
        class="tld-lead__img"
        :src="characterThumbnailUrl(character.id)"
        alt=""
        @error="broken.add(`character:${character.id}`)"
      />
      <span
        v-else
        class="tld-lead__img tld-source__img--blank"
        aria-hidden="true"
      >
        <v-icon size="20">mdi-account-outline</v-icon>
      </span>
      <div class="tld-lead__text">
        <b>Character LoRA of {{ character.name }}</b>
        <small>Learns this person's face and likeness.</small>
      </div>
      <div class="tld-lead__src">
        <span class="tld-inline-label">Train on</span>
        <Segmented
          full
          aria-label="Train on"
          :options="trainOnOptions"
          :model-value="store.trainOn"
          @update:model-value="store.setTrainOn"
        />
      </div>
    </div>

    <!-- An ordinary set could hold anything, so it is asked. -->
    <div v-else-if="store.dataset" class="tld-field">
      <div class="tld-label-row">
        <FieldLabel :id="subjectLabelId">What is this set of?</FieldLabel>
        <PinnedChip :keys="pinnedBy.subject" @unpin="store.unpin" />
      </div>
      <div
        role="radiogroup"
        class="tld-opts"
        :aria-labelledby="subjectLabelId"
        @keydown="onSubjectKey"
      >
        <button
          v-for="o in SUBJECT_OPTIONS"
          :key="o.id"
          type="button"
          role="radio"
          class="tld-opt"
          :class="{ 'tld-opt--on': o.id === store.subject }"
          :aria-checked="o.id === store.subject ? 'true' : 'false'"
          :tabindex="o.id === subjectTabStop ? 0 : -1"
          :data-testid="`tld-subject-${o.id}`"
          @click="store.subject = o.id"
        >
          <v-icon size="20" class="tld-opt__icon">{{ o.icon }}</v-icon>
          <span>
            <b>{{ o.label }}</b>
            <small>{{ o.detail }}</small>
          </span>
        </button>
      </div>
    </div>

    <div class="tld-g2">
      <div class="tld-field">
        <FieldLabel>Trigger word</FieldLabel>
        <AppInput
          v-model="store.triggerWord"
          mono
          aria-label="Trigger word"
          :error="triggerError"
          data-testid="tld-trigger"
        />
        <span class="tld-hint">{{ triggerHint }}</span>
      </div>
      <div class="tld-field">
        <div class="tld-label-row">
          <FieldLabel>Base model</FieldLabel>
          <PinnedChip :keys="pinnedBy.model" @unpin="store.unpin" />
        </div>
        <AppSelect
          v-model="store.selectedPreset"
          label="Base model"
          hide-label
          :options="presetOptions"
          :disabled="store.presetsLoading && !presetOptions.length"
        />
        <span v-if="presetVerdict && presetVerdict !== 'fits'" class="tld-hint">
          {{ VERDICT_LABELS[presetVerdict] }}
        </span>
        <span class="tld-hint">
          <button
            type="button"
            class="tld-link"
            :aria-busy="store.presetCheckBusy"
            :aria-disabled="store.presetCheckBusy"
            data-testid="tld-check-presets"
            @click="onCheckPresets"
          >
            Check for recipe updates
          </button>
          <span role="status" data-testid="tld-check-presets-status">{{
            store.presetCheckBusy
              ? " Checking pixlstash.dev…"
              : store.presetCheckMessage && ` ${store.presetCheckMessage}`
          }}</span>
        </span>
      </div>
    </div>

    <!-- Asked once, after the first check: whether to check every day. -->
    <div
      v-if="askAutoCheck"
      class="tld-notice tld-notice--ask"
      role="group"
      aria-labelledby="tld-auto-check-q"
      data-testid="tld-auto-check-ask"
    >
      <span id="tld-auto-check-q" class="tld-sp">
        Check for recipe updates automatically, once a day?
      </span>
      <AppButton size="sm" variant="secondary" @click="answerAutoCheck(true)">
        Yes
      </AppButton>
      <AppButton size="sm" variant="ghost" @click="answerAutoCheck(false)">
        No
      </AppButton>
    </div>
    <p v-else-if="autoCheckNote" class="tld-hint" role="status" data-testid="tld-auto-check-note">
      {{ autoCheckNote }}
    </p>

    <div class="tld-field">
      <div class="tld-label-row">
        <FieldLabel :id="effortLabelId">How hard to train</FieldLabel>
        <PinnedChip :keys="pinnedBy.effort" @unpin="store.unpin" />
      </div>
      <Segmented
        v-model="store.effort"
        full
        :options="EFFORT_OPTIONS"
        aria-label="How hard to train"
      />
      <span class="tld-hint">
        <span v-if="effortLine" class="tld-readout" data-testid="tld-effort-readout">{{
          effortLine
        }}</span>
        <span v-else-if="!store.effectiveSubject" class="tld-readout tld-readout--quiet"
          >Say what the set is of to see what this sets.</span
        >
        {{ effortHintText }}
      </span>
    </div>

    <div class="tld-field">
      <div class="tld-label-row">
        <FieldLabel>Fit to this GPU</FieldLabel>
        <PinnedChip :keys="pinnedBy.fit" @unpin="store.unpin" />
      </div>
      <Segmented
        v-model="store.fit"
        full
        variant="icon-label"
        :options="fitOptions"
        aria-label="Fit to this GPU"
      />
      <span class="tld-hint">
        <span v-if="fitLine" class="tld-readout" data-testid="tld-fit-readout">{{
          fitLine
        }}</span>
        <template v-if="store.fit === 'best_quality' && values?.['model.quantize']?.value">
          This model's weights come in 8-bit already, so this keeps as much of
          it on the GPU as fits instead.
        </template>
        <template v-else-if="store.fit === 'best_quality'">
          Keeps the model's weights at full precision. It needs more memory and
          can run slower; it does not promise a better LoRA.
        </template>
      </span>
    </div>

    <!-- Previews cost time here (#2415): asked only when another route is
         clearly faster than drawing them while training. -->
    <div v-if="previewOptions.length" class="tld-field" data-testid="tld-previews">
      <div class="tld-label-row">
        <FieldLabel :id="previewsLabelId">Previews</FieldLabel>
        <PinnedChip :keys="pinnedBy.previews" @unpin="pickPreviews('while')" />
      </div>
      <div
        role="radiogroup"
        class="tld-opts"
        :aria-labelledby="previewsLabelId"
        @keydown="onPreviewsKey"
      >
        <button
          v-for="o in previewOptions"
          :key="o.id"
          type="button"
          role="radio"
          class="tld-opt"
          :class="{ 'tld-opt--on': o.id === previewChoice.selected }"
          :aria-checked="o.id === previewChoice.selected ? 'true' : 'false'"
          :tabindex="o.id === previewsTabStop ? 0 : -1"
          :data-testid="`tld-previews-${o.id}`"
          @click="pickPreviews(o.id)"
        >
          <v-icon size="20" class="tld-opt__icon">{{ PREVIEW_ICONS[o.id] }}</v-icon>
          <span>
            <b>{{ o.label }}</b>
            <small>{{ o.detail }}</small>
            <span class="tld-price">{{ o.time }}</span>
            <span class="tld-price tld-price--next"
              >{{ o.peak }} <em>· {{ o.free }}</em></span
            >
            <small v-if="o.provenance" class="tld-price--next">{{ o.provenance }}</small>
            <small v-if="o.tight" class="tld-price--next tld-price--tight"
              >Tight on this GPU</small
            >
          </span>
        </button>
      </div>
      <span class="tld-hint">{{ previewsHint }}</span>
    </div>

    <!-- A recipe that trains on clips asks whether their sound trains too. -->
    <div v-if="soundOn !== null" class="tld-field" data-testid="tld-sound">
      <FieldLabel>Sound</FieldLabel>
      <Segmented
        full
        aria-label="Sound"
        :options="SOUND_OPTIONS"
        :model-value="soundOn ? 'sound' : 'silent'"
        @update:model-value="(id) => store.setPin(AUDIO_KEY, id === 'sound')"
      />
      <span class="tld-hint">
        {{
          soundOn
            ? "Clips train with their soundtrack, so the LoRA learns how they sound as well as how they move."
            : "Clips train without their soundtrack; only how they look and move is learned."
        }}
      </span>
    </div>

    <p
      v-if="planError"
      class="tld-notice tld-notice--bad"
      role="alert"
      data-testid="tld-plan-error"
    >
      <v-icon size="16">mdi-alert-circle-outline</v-icon>
      <span>{{ planError }}</span>
    </p>

    <!-- Most captions describe the person the same way: the LoRA would learn
         that everyone so described is them, unless the words are kept
         generic (ai-toolkit's output preservation). -->
    <div
      v-if="keepGeneric"
      class="tld-notice tld-notice--warn tld-keep"
      data-testid="tld-keep-generic"
    >
      <v-icon size="16">mdi-alert-outline</v-icon>
      <div class="tld-keep__body">
        <span>
          <b>{{ keepGeneric.headline }}</b> Left alone, the LoRA learns that every
          {{ keepGeneric.bare }} is {{ keepGeneric.name }}. Keeping the words
          generic holds &ldquo;{{ keepGeneric.description }}&rdquo; to what the
          model drew before training.
        </span>
        <span v-if="keepGeneric.example" class="tld-keep__cap" data-testid="tld-keep-caption"
          >{{ keepGeneric.example[0] }}<mark v-if="keepGeneric.example[1]">{{
            keepGeneric.example[1]
          }}</mark>{{ keepGeneric.example[2] }}</span
        >
        <div class="tld-keep__row">
          <v-switch
            :model-value="keepGeneric.on"
            color="primary"
            density="compact"
            hide-details
            :aria-label="keepGeneric.label"
            data-testid="tld-keep-switch"
            @update:model-value="(v) => store.setPin(PRESERVATION_KEY, !!v)"
          />
          <span class="tld-keep__label">
            <span>{{ keepGeneric.label }}</span>
            <span class="tld-hint">slower steps, a little less likeness</span>
          </span>
          <span class="tld-sp" />
          <span class="tld-hint" data-testid="tld-keep-source">{{ keepGeneric.source }}</span>
        </div>
      </div>
    </div>

    <!-- Checks before Start: warnings, never a block (but an empty set). -->
    <ul v-if="checks.length" class="tld-checks" data-testid="tld-checks">
      <li
        v-for="c in checks"
        :key="c.id"
        class="tld-notice"
        :class="c.bad ? 'tld-notice--bad' : 'tld-notice--warn'"
      >
        <v-icon size="16">{{
          c.bad ? "mdi-alert-circle-outline" : "mdi-alert-outline"
        }}</v-icon>
        <span>{{ c.text }}</span>
      </li>
    </ul>

    <!-- Tier 2: every value the run will use, and where it came from. -->
    <section class="tld-more">
      <button
        type="button"
        class="tld-more__toggle"
        :aria-expanded="advancedOpen ? 'true' : 'false'"
        :aria-controls="advancedId"
        data-testid="tld-advanced"
        @click="advancedOpen = !advancedOpen"
      >
        <v-icon
          size="16"
          class="tld-more__caret"
          :class="{ 'tld-more__caret--open': advancedOpen }"
          >mdi-chevron-right</v-icon
        >
        <span>Advanced</span>
        <span class="tld-sp" />
        <span v-if="pinnedCount" class="tld-more__note"
          >{{ pinnedCount }} pinned by you</span
        >
      </button>
      <div
        v-if="advancedOpen"
        :id="advancedId"
        class="tld-more__body"
        @keydown.enter="keepEnterInField"
      >
        <p v-if="!values" class="tld-hint">
          {{
            planError
              ? "The settings show once the run fits."
              : "Say what the set is of to see every setting."
          }}
        </p>
        <table v-else class="tld-table">
          <thead>
            <tr>
              <th scope="col">Setting</th>
              <th scope="col">Value</th>
              <th scope="col">Comes from</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="row in visibleRows" :key="row.label">
              <tr
                :class="{ 'tld-row--pinned': rowPinned(row) }"
                :data-testid="`tld-row-${row.keys[0]}`"
              >
                <th scope="row">{{ row.label }}</th>
                <td>
                  <span v-if="row.prompts" class="tld-quiet">{{
                    promptsSummary
                  }}</span>
                  <span
                    v-else
                    class="tld-cell"
                    :class="{ 'tld-cell--pair': row.keys.length > 1 }"
                  >
                    <template v-for="key in row.keys" :key="key">
                      <span v-if="!values[key]" class="tld-quiet">—</span>
                      <v-switch
                        v-else-if="rules[key]?.kind === 'bool'"
                        :model-value="values[key].value"
                        color="primary"
                        density="compact"
                        hide-details
                        :aria-label="keyLabel(key)"
                        @update:model-value="(v) => store.setPin(key, !!v)"
                      />
                      <AppSelect
                        v-else-if="rules[key]?.kind === 'enum'"
                        compact
                        hide-label
                        :label="keyLabel(key)"
                        :model-value="values[key].value"
                        :options="rules[key].choices"
                        @update:model-value="(v) => store.setPin(key, v)"
                      />
                      <span
                        v-else-if="rules[key]?.kind === 'int_list'"
                        class="tld-sizes"
                        role="group"
                        :aria-label="keyLabel(key)"
                      >
                        <button
                          v-for="size in sizeChoices(values[key].value)"
                          :key="size"
                          type="button"
                          class="tld-chip"
                          :class="{
                            'tld-chip--on': values[key].value.includes(size),
                          }"
                          :aria-pressed="
                            values[key].value.includes(size) ? 'true' : 'false'
                          "
                          @click="toggleSize(key, size)"
                        >
                          {{ size }}
                        </button>
                      </span>
                      <AppInput
                        v-else
                        mono
                        :aria-label="keyLabel(key)"
                        :model-value="fieldText(key)"
                        :error="fieldErrors[key] || false"
                        @focusin="onFieldFocus(key)"
                        @update:model-value="(v) => onFieldInput(key, v)"
                        @blur="onFieldBlur(key)"
                      />
                    </template>
                  </span>
                </td>
                <td class="tld-from">
                  <span class="tld-from__inner">
                    <span>{{ rowSource(row) }}</span>
                    <button
                      v-if="rowPinned(row)"
                      type="button"
                      class="tld-reset"
                      :aria-label="`Back to the derived ${row.label.toLowerCase()}`"
                      @click="store.unpin(row.keys)"
                    >
                      <v-icon size="12">mdi-restore</v-icon>
                      <span>{{ resetLabel(row) }}</span>
                    </button>
                  </span>
                </td>
              </tr>
              <tr v-if="row.prompts" class="tld-prompts-row">
                <td colspan="3">
                  <div
                    v-for="(line, i) in store.prompts"
                    :key="i"
                    class="tld-prompt"
                    data-testid="tld-prompt"
                  >
                    <button
                      type="button"
                      class="tld-chip tld-chip--word"
                      :class="{ 'tld-chip--on': hasTrigger(line) }"
                      :aria-pressed="hasTrigger(line) ? 'true' : 'false'"
                      :aria-label="
                        hasTrigger(line)
                          ? `Leave ${triggerShown} out of this prompt`
                          : `Add ${triggerShown} to this prompt`
                      "
                      @click="store.togglePromptTrigger(i)"
                    >
                      <v-icon v-if="hasTrigger(line)" size="12">mdi-check</v-icon>
                      <span>{{ triggerShown }}</span>
                    </button>
                    <AppInput
                      :aria-label="`Sample prompt ${i + 1}`"
                      :model-value="store.promptDisplay(line)"
                      @update:model-value="(v) => store.setPromptText(i, v)"
                    />
                    <button
                      type="button"
                      class="tld-remove"
                      :aria-label="`Remove sample prompt ${i + 1}`"
                      :disabled="store.prompts.length <= 1"
                      @click="store.removePrompt(i)"
                    >
                      <v-icon size="16">mdi-close</v-icon>
                    </button>
                    <span
                      v-if="lineRole(line)"
                      class="tld-prompt-role"
                      data-testid="tld-prompt-role"
                    >
                      <span class="tld-role">{{ lineRole(line).label }}</span>
                      <span class="tld-hint">{{ lineRole(line).text }}</span>
                    </span>
                  </div>
                  <AppButton
                    size="sm"
                    variant="ghost"
                    icon-left="plus"
                    data-testid="tld-add-prompt"
                    @click="store.addPrompt()"
                    >Add a prompt</AppButton
                  >
                  <span v-if="hasRoles" class="tld-hint tld-prompts-note">
                    Prompts without
                    <span class="tld-mono">{{ triggerShown }}</span> show what the
                    LoRA does to pictures it wasn't asked for: one of someone like
                    {{ store.displayName || pronoun }}, and one of someone who is
                    not. Click the word on a prompt to add or leave it out.
                  </span>
                  <span v-else class="tld-hint tld-prompts-note">
                    Prompts without
                    <span class="tld-mono">{{ triggerShown }}</span> show whether
                    the LoRA changes pictures it wasn't asked for. Click the word
                    on a prompt to add or leave it out.
                  </span>
                </td>
              </tr>
            </template>
            <tr v-if="extraRows.length">
              <td colspan="3" class="tld-more-rows">
                <AppButton size="sm" variant="ghost" @click="showAll = !showAll">
                  {{ showAll ? "Show fewer" : `Show ${extraRows.length} more` }}
                </AppButton>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <template #footer>
      <span class="tld-foot" :class="{ 'tld-foot--bad': footError }" role="status">
        <v-icon size="16">{{
          footError ? "mdi-alert-circle-outline" : "mdi-information-outline"
        }}</v-icon>
        <span data-testid="tld-foot-text">{{ footError || footLine }}</span>
      </span>
      <AppButton key-hint="esc" :disabled="store.submitting" @click="store.closeDialog()"
        >Cancel</AppButton
      >
      <AppButton
        variant="primary"
        key-hint="enter"
        :loading="store.submitting"
        :disabled="!canStart"
        data-testid="tld-start"
        @click="onStart"
        >Start training</AppButton
      >
    </template>
  </AppDialog>
</template>

<script setup>
import { computed, nextTick, reactive, ref, useId, watch } from "vue";
import { VIcon, VSwitch } from "vuetify/components";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import Segmented from "../widgets/Segmented.vue";
import PinnedChip from "./TrainLoraPinnedChip.vue";
import { useRouter } from "vue-router";
import { characterThumbnailUrl } from "../../api/characters";
import { pictureSetThumbnailUrl } from "../../api/pictureSets";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useTrainingRunsStore } from "../../stores/useTrainingRunsStore";
import { useTrainingStore } from "../../stores/useTrainingStore";
import { arrowStep, tabStopId } from "../../utils/radioGroup.js";
import {
  EFFORT_LABELS,
  FIT_LABELS,
  PRIMARY_ROWS,
  AUDIO_KEY,
  PRESERVATION_KEY,
  PRESERVATION_CLASS_KEY,
  pronounOf,
  promptRole,
  roleText,
  PREVIEW_SIZE_KEYS,
  SAMPLING_KEY,
  PROMPTS_KEY,
  SIZE_CHOICES,
  VERDICT_LABELS,
  choiceOfSource,
  effortHint,
  effortReadout,
  fitReadout,
  formatCount,
  formatValue,
  gb,
  hasTrigger,
  keyLabel,
  moreRows,
  parseTyped,
  previewCards,
  previewHint,
  sourceLabel,
  timeEstimate,
} from "../../utils/trainingPlan";

const emit = defineEmits(["started"]);

const store = useTrainingStore();
const lists = useEntityListsStore();
const router = useRouter();

const sourceLabelId = useId();
const subjectLabelId = useId();
const effortLabelId = useId();
const previewsLabelId = useId();
const advancedId = useId();

const SUBJECT_OPTIONS = [
  {
    id: "person",
    label: "A person",
    icon: "mdi-account-outline",
    detail: "Face and likeness. Backgrounds and clothes stay free.",
  },
  {
    id: "style",
    label: "A style",
    icon: "mdi-palette-outline",
    detail: "Light, palette and finish, whatever the subject.",
  },
  {
    id: "thing",
    label: "A thing",
    icon: "mdi-cube-outline",
    detail: "An object, outfit or place, kept recognisable.",
  },
];
const PREVIEW_ICONS = {
  while: "mdi-image-multiple-outline",
  smaller: "mdi-image-size-select-small",
  after: "mdi-clock-outline",
};
// The owner's pick per recipe, kept in this browser: a non-default pick comes
// back as the same pins the next time the question is asked (#2415).
const PREVIEWS_STORAGE_KEY = "pixlstash.train.previews";
const SOUND_OPTIONS = [
  { id: "sound", label: "Learn the sound too" },
  { id: "silent", label: "Picture only" },
];
const EFFORT_OPTIONS = Object.entries(EFFORT_LABELS).map(([id, label]) => ({
  id,
  label,
}));

const advancedOpen = ref(false);
const showAll = ref(false);
// Each typed field keeps its text only while it has focus; otherwise it
// shows the resolved value.
const drafts = reactive({});
const fieldErrors = reactive({});

// A fresh dialog starts folded, with no stale field text.
watch(
  () => store.trainDialogOpen,
  (open) => {
    if (!open) return;
    advancedOpen.value = false;
    showAll.value = false;
    for (const key of Object.keys(drafts)) delete drafts[key];
    for (const key of Object.keys(fieldErrors)) delete fieldErrors[key];
    previewsRestored.clear();
  },
);

const character = computed(() => store.character);
const plan = computed(() => store.plan);
const resolution = computed(() => plan.value?.resolution || null);
const values = computed(() => resolution.value?.values || null);
const rules = computed(() => plan.value?.rules || {});
const derived = computed(() => plan.value?.derived || {});
const planError = computed(() => plan.value?.error?.message || "");
const previewChoice = computed(() => plan.value?.previews || null);
const previewOptions = computed(() => previewCards(previewChoice.value));
const previewsHint = computed(() => previewHint(previewChoice.value));
const previewsTabStop = computed(() =>
  tabStopId(previewOptions.value, previewChoice.value?.selected),
);

function readRememberedPreviews() {
  try {
    return JSON.parse(localStorage.getItem(PREVIEWS_STORAGE_KEY) || "{}") || {};
  } catch (err) {
    console.warn("Could not read the remembered Previews choices:", err);
    return {};
  }
}

function rememberPreviews(presetId, id) {
  try {
    const all = readRememberedPreviews();
    if (id === "while") delete all[presetId];
    else all[presetId] = id;
    localStorage.setItem(PREVIEWS_STORAGE_KEY, JSON.stringify(all));
  } catch (err) {
    console.warn("Could not remember the Previews choice:", err);
  }
}

/** Answer the Previews question with the pins its route stands for. */
function pickPreviews(id) {
  const choice = previewChoice.value;
  if (!choice) return;
  // Only the 768² pins are the question's (a size of any other kind hides the
  // smaller route), and a typed "previews after training: no" is kept.
  const drop = choice.selected === "smaller" ? [...PREVIEW_SIZE_KEYS] : [];
  if (id !== "after" && store.pins[SAMPLING_KEY]) drop.push(SAMPLING_KEY);
  store.unpin(drop);
  if (id === "after") store.setPin(SAMPLING_KEY, true);
  if (id === "smaller") {
    for (const key of PREVIEW_SIZE_KEYS) store.setPin(key, choice.smaller_size);
  }
  rememberPreviews(choice.preset, id);
}

function onPreviewsKey(event) {
  const id = arrowStep(event, previewOptions.value, previewChoice.value?.selected);
  if (id !== undefined) pickPreviews(id);
}

// The remembered pick is put back once per opening, the first time the
// question is asked for that recipe; after that the owner's clicks rule.
const previewsRestored = new Set();
watch(previewChoice, (choice) => {
  if (!choice || previewsRestored.has(choice.preset)) return;
  previewsRestored.add(choice.preset);
  const id = readRememberedPreviews()[choice.preset];
  // A value the owner typed into the question's own keys wins over memory.
  const typed = [SAMPLING_KEY, ...PREVIEW_SIZE_KEYS].some((k) => k in store.pins);
  if (
    choice.selected === "while" &&
    !typed &&
    id &&
    choice.options.some((o) => o.id === id)
  ) {
    pickPreviews(id);
  }
});
/** The Sound choice, or null when the recipe trains on no clips. */
const soundOn = computed(() => {
  const entry = values.value?.[AUDIO_KEY];
  return store.presetClips && entry ? !!entry.value : null;
});

const subtitle = computed(() => {
  if (!store.dataset) return store.scopeName;
  const n = store.pictureCount;
  return `${store.displayName} · ${formatCount(n)} picture${n === 1 ? "" : "s"}`;
});

const trainOnOptions = computed(() => {
  const ch = character.value;
  if (!ch) return [];
  // Opened on a set under the person (#2124): that set first.
  const child = store.childSet;
  // A recipe that trains on clips counts them too.
  const n = (count, clips) => count + (store.presetClips ? clips || 0 : 0);
  const reference = n(ch.reference_count, ch.reference_clips);
  return [
    ...(child
      ? [
          {
            id: "set",
            label: `${child.name} · ${formatCount(n(child.count, child.clips))}`,
          },
        ]
      : []),
    {
      id: "reference",
      label: `Reference set · ${formatCount(reference)}`,
      disabled: ch.reference_set_id == null || reference === 0,
    },
    {
      id: "all",
      label: `Every picture of ${ch.name} · ${formatCount(n(ch.all_count, ch.all_clips))}`,
    },
  ];
});

// ── Train on: the picker the Models workspace's door opens on ────────────

watch(
  () => store.trainDialogOpen && store.pickable,
  (picking) => {
    if (!picking) return;
    // Cached lists draw at once; the refresh swaps in fresh counts.
    lists.refresh("characters");
    lists.refresh("sets");
  },
  { immediate: true },
);

const plural = (n) => `${formatCount(n)} picture${n === 1 ? "" : "s"}`;

const sourceOptions = computed(() => [
  ...lists.characters.map((c) => ({
    id: `character:${c.id}`,
    type: "character",
    entityId: Number(c.id),
    name: c.name,
    detail: `Person · ${plural(c.image_count ?? 0)}`,
    thumb: characterThumbnailUrl(c.id),
  })),
  // A reference set trains as its person, who is already listed.
  ...lists.pictureSets
    .filter((set) => !set.reference_character)
    .map((set) => ({
      id: `set:${set.id}`,
      type: "set",
      entityId: Number(set.id),
      name: set.name,
      detail: `Set · ${plural(set.picture_count ?? 0)}`,
      // An empty set has no thumbnail: the route answers 404.
      thumb: set.picture_count ? pictureSetThumbnailUrl(set.id) : "",
    })),
]);

/** Sources whose thumbnail would not load: they keep their tile, with a glyph. */
const broken = reactive(new Set());
// The dialog stays mounted between opens: each open retries every thumbnail.
watch(
  () => store.trainDialogOpen,
  (open) => {
    if (open) broken.clear();
  },
);

const pickedSource = computed(() =>
  store.scopeType && store.scopeId != null
    ? `${store.scopeType}:${store.scopeId}`
    : null,
);

const sourceTabStop = computed(() =>
  tabStopId(sourceOptions.value, pickedSource.value),
);

function pickSource(option) {
  if (option.id === pickedSource.value) return;
  store.pickScope({ type: option.type, id: option.entityId, name: option.name });
}

function onSourceKey(event) {
  const group = event.currentTarget;
  const id = arrowStep(event, sourceOptions.value, pickedSource.value);
  const option = sourceOptions.value.find((o) => o.id === id);
  if (!option) return;
  pickSource(option);
  // The roving stop moves with the choice, and focus with it.
  nextTick(() =>
    group?.querySelector(`[data-testid="tld-source-${option.id}"]`)?.focus(),
  );
}

const subjectTabStop = computed(() =>
  tabStopId(SUBJECT_OPTIONS, store.subject),
);
function onSubjectKey(event) {
  const id = arrowStep(event, SUBJECT_OPTIONS, store.subject);
  if (id !== undefined) store.subject = id;
}

const triggerShown = computed(() => store.triggerWord.trim() || "the trigger word");
const triggerError = computed(() =>
  store.triggerWord.trim() ? false : "Give the LoRA a trigger word.",
);
const withTrigger = computed(() => store.prompts.filter(hasTrigger).length);
const triggerHint = computed(() => {
  const from = character.value
    ? "From the character's name. "
    : store.scopeType === "set"
      ? "From the set's name. "
      : "";
  const total = store.prompts.length;
  if (!total) return from.trim();
  return `${from}Goes into ${withTrigger.value} of the ${total} sample prompt${total === 1 ? "" : "s"}.`;
});
const promptsSummary = computed(
  () =>
    `${store.prompts.length} · ${withTrigger.value} with the trigger word`,
);

/** Whether the run keeps the person's description generic, as resolved. */
const keptGeneric = computed(() => !!values.value?.[PRESERVATION_KEY]?.value);
const pronoun = computed(() => pronounOf(store.promptsInfo?.description));

/**
 * The notice over the run's settings when most captions describe the person
 * the same way, with the switch that keeps those words generic. Null when
 * they do not, or before the plan carries the switch.
 */
const keepGeneric = computed(() => {
  const info = store.promptsInfo;
  const entry = values.value?.[PRESERVATION_KEY];
  if (!info?.keep_generic || !entry) return null;
  const description = values.value?.[PRESERVATION_CLASS_KEY]?.value || info.description;
  const name = store.displayName || "them";
  const count = info.description_count;
  const total = info.caption_count;
  const headline =
    total === 1
      ? `The caption calls ${name} “${description}”.`
      : count === total
        ? `All ${formatCount(total)} captions call ${name} “${description}”.`
        : `${formatCount(count)} of ${formatCount(total)} captions call ${name} “${description}”.`;
  let example = null;
  if (info.description_example) {
    const text = info.description_example;
    const at = text.toLowerCase().indexOf(description.toLowerCase());
    example =
      at < 0
        ? [text, "", ""]
        : [
            text.slice(0, at),
            text.slice(at, at + description.length),
            text.slice(at + description.length),
          ];
  }
  let source;
  if (PRESERVATION_KEY in store.allPins) source = "Set by you";
  else if (entry.value) source = `On because ${count} of ${total} captions use it`;
  else source = "Off: training the text encoder cannot keep words generic";
  return {
    headline,
    description,
    bare: description.replace(/^an? /i, ""),
    name,
    example,
    on: !!entry.value,
    label: `Keep “${description}” generic`,
    source,
  };
});

/** A prompt line's control role and what it should do, or null. */
function lineRole(line) {
  const role = promptRole(line.text, store.promptsInfo);
  const words = roleText(role, {
    kept: keptGeneric.value,
    description: store.promptsInfo?.description,
  });
  if (!words) return null;
  return {
    label: words.label,
    text: role === "lookalike" ? `${words.expect}: ${words.detail}` : words.expect,
  };
}
const hasRoles = computed(() => store.prompts.some((line) => lineRole(line)));

const presetOptions = computed(() => {
  const verdicts = Object.fromEntries(
    (plan.value?.presets || []).map((p) => [p.id, p.verdict]),
  );
  return (store.presets || []).map((p) => {
    const verdict = verdicts[p.id] || (p.offered ? null : "needs_checkout");
    return {
      label: p.label,
      value: p.id,
      name: p.label,
      chips: verdict ? [VERDICT_LABELS[verdict]] : [],
    };
  });
});
const presetVerdict = computed(
  () =>
    (plan.value?.presets || []).find((p) => p.id === store.selectedPreset)
      ?.verdict || null,
);
const presetLabel = computed(
  () =>
    (store.presets || []).find((p) => p.id === store.selectedPreset)?.label ||
    "",
);

const fitOptions = computed(() => {
  const free = plan.value?.room?.vram_mb;
  return [
    {
      id: "auto",
      icon: "chip",
      label: free != null ? `Auto · ${gb(free)} GB free` : "Auto",
    },
    { id: "save_memory", label: FIT_LABELS.save_memory },
    { id: "best_quality", label: FIT_LABELS.best_quality },
  ];
});

const effortLine = computed(() =>
  effortReadout({
    values: values.value,
    clamp: plan.value?.steps_clamp,
    effort: store.effort,
    pictureCount: store.pictureCount,
  }),
);
const effortHintText = computed(() =>
  effortHint({
    rule: plan.value?.steps_rule,
    clamp: plan.value?.steps_clamp,
    effort: store.effort,
    subject: store.effectiveSubject,
  }),
);
const fitLine = computed(() => fitReadout(resolution.value));

const sourceCtx = computed(() => ({
  effort: store.effort,
  fit: store.fit,
  subject: store.effectiveSubject,
  character: character.value,
  pictureCount: store.pictureCount,
  clamp: plan.value?.steps_clamp,
  presetLabel: presetLabel.value,
  room: plan.value?.room,
}));

/** Which pinned keys each choice set: its "N pinned · unpin". */
const pinnedBy = computed(() => {
  const out = { subject: [], effort: [], fit: [], model: [], previews: [] };
  const asked = previewChoice.value;
  for (const key of Object.keys(store.allPins)) {
    // The Sound choice and the keep-generic switch are their own controls,
    // not pins on the recipe.
    if (key === AUDIO_KEY || key === PRESERVATION_KEY) continue;
    // The Previews question's own pins sit beside it.
    if (
      asked &&
      (key === SAMPLING_KEY ||
        (asked.selected === "smaller" && PREVIEW_SIZE_KEYS.includes(key)))
    ) {
      out.previews.push(key);
      continue;
    }
    const source =
      key === PROMPTS_KEY ? "subject" : derived.value[key]?.source || "model";
    const choice = choiceOfSource(source);
    if (choice) out[choice].push(key);
  }
  return out;
});
const pinnedCount = computed(() => Object.keys(store.allPins).length);

const extraRows = computed(() => moreRows(values.value));
const visibleRows = computed(() =>
  showAll.value ? [...PRIMARY_ROWS, ...extraRows.value] : PRIMARY_ROWS,
);

function rowPinned(row) {
  return row.keys.some((key) => key in store.allPins);
}

function rowSource(row) {
  if (row.prompts) {
    return store.promptsPinned
      ? "Typed by you, so it stays put"
      : sourceLabel({ source: "subject" }, sourceCtx.value);
  }
  if (rowPinned(row)) return "Typed by you, so it stays put";
  return sourceLabel(values.value?.[row.keys[0]], sourceCtx.value);
}

function resetLabel(row) {
  if (row.prompts) return "Written";
  return row.keys
    .filter((key) => key in store.allPins)
    .map((key) => {
      const v = derived.value[key]?.value;
      return typeof v === "number" ? formatCount(v) : formatValue(v);
    })
    .join(" / ");
}

function fieldText(key) {
  if (key in drafts) return drafts[key];
  return formatValue(values.value?.[key]?.value);
}

function onFieldFocus(key) {
  drafts[key] = formatValue(values.value?.[key]?.value);
}

function onFieldInput(key, text) {
  drafts[key] = text;
  const parsed = parseTyped(rules.value[key], text);
  if (parsed.error) {
    fieldErrors[key] = parsed.error;
    return;
  }
  delete fieldErrors[key];
  // Blank goes back to the derived value.
  store.setPin(key, parsed.value);
}

function onFieldBlur(key) {
  delete drafts[key];
  // A field left invalid is not a pin; it shows the run's value again.
  delete fieldErrors[key];
}

function sizeChoices(current) {
  return [...new Set([...SIZE_CHOICES, ...(current || [])])].sort(
    (a, b) => a - b,
  );
}

function toggleSize(key, size) {
  const current = values.value?.[key]?.value || [];
  const next = current.includes(size)
    ? current.filter((s) => s !== size)
    : [...current, size].sort((a, b) => a - b);
  // A run trains at one size at least.
  if (next.length) store.setPin(key, next);
}

const checks = computed(() => {
  const d = store.dataset;
  if (!d) return [];
  const out = [];
  const n = store.pictureCount;
  const clips = d.clips || 0;
  if (n === 0) {
    out.push({
      id: "none",
      bad: true,
      text: clips
        ? `There are no still pictures to train on. This set's ${clips} video clip${clips === 1 ? "" : "s"} train only with a motion recipe.`
        : "There are no pictures to train on.",
    });
    return out;
  }
  if (n < d.min_pictures) {
    out.push({
      id: "few",
      text: `Only ${n} picture${n === 1 ? "" : "s"}. A LoRA usually needs ${d.min_pictures} or more, and with fewer it tends to copy them rather than learn from them.`,
    });
  }
  if (clips > 0 && !store.presetClips) {
    out.push({
      id: "clips",
      text: `${clips} video clip${clips === 1 ? " is" : "s are"} left out: this model trains on still pictures. A motion recipe trains on clips.`,
    });
  } else if (clips === 0 && store.presetClips) {
    out.push({
      id: "no-clips",
      text: "There are no video clips here, so this learns from still pictures alone and no motion.",
    });
  }
  if (d.untagged > 0) {
    out.push({
      id: "untagged",
      text: `${d.untagged} picture${d.untagged === 1 ? " has" : "s have"} no tags yet, so ${d.untagged === 1 ? "it trains" : "they train"} on the trigger word alone.`,
    });
  }
  if (d.near_duplicates > 0) {
    out.push({
      id: "near",
      text: `${d.near_duplicates} pictures are near copies of each other. Copies count twice, so the LoRA leans towards them.`,
    });
  }
  if (d.other_people > 0) {
    out.push({
      id: "people",
      text: `${d.other_people} picture${d.other_people === 1 ? " shows" : "s show"} somebody else's face too, which the LoRA may learn as well.`,
    });
  }
  return out;
});

/** The first reason Start is unavailable, said in the footer. */
const blocker = computed(() => {
  if (store.configLoaded && !store.aiToolkitConfigured)
    return "Set up AI-toolkit to train.";
  if (store.pickable && !store.scopeType) return "Pick a person or a set to train on.";
  if (!store.dataset) return store.datasetLoading ? "" : "Nothing to train on.";
  if (store.pictureCount === 0) return "There are no pictures to train on.";
  if (!store.effectiveSubject) return "Say what the set is of.";
  if (!store.triggerWord.trim()) return "Give the LoRA a trigger word.";
  if (presetVerdict.value === "needs_checkout")
    return "This AI-toolkit checkout cannot train this model. Update it first.";
  if (store.prompts.length && !store.prompts.some((l) => l.text.trim()))
    return "Keep at least one sample prompt.";
  if (Object.keys(fieldErrors).length) return "Fix the Advanced value marked in red.";
  if (planError.value) return planError.value;
  if (!resolution.value) return "";
  return "";
});

const footError = computed(() => store.submitError || blocker.value);

/** "About 50 min. Other GPU work pauses while it runs." */
const footLine = computed(() =>
  [timeEstimate(resolution.value), "Other GPU work pauses while it runs."]
    .filter(Boolean)
    .join(" "),
);

// The plan on screen must answer the choices now made: a plan for earlier
// choices (the debounce has not fired yet) or a dataset still loading would
// put a run on screen that Start does not queue.
const canStart = computed(
  () =>
    !blocker.value &&
    !!resolution.value &&
    plan.value?.choicesKey === store.choicesKey &&
    !store.datasetLoading &&
    !store.planLoading &&
    !store.submitting,
);

async function onStart() {
  if (!canStart.value) return;
  try {
    const job = await store.startTraining();
    const fromModels = store.pickable;
    store.closeDialog();
    // The new run's card is on Training runs; from the Models workspace's
    // door that is where Start lands.
    useTrainingRunsStore().loadJobs();
    if (fromModels) router.push({ name: "models-runs" });
    // The create response carries the resolver's result as an object.
    const profile = job?.resolved_profile?.profile_label;
    store.showQueuedToast(
      profile
        ? `Training queued: ${profile}. Watch progress under Tasks in the title bar.`
        : "Training queued. Watch progress under Tasks in the title bar.",
    );
    emit("started");
  } catch (err) {
    // store.submitError is already shown in the footer; keep the cause too.
    console.warn(
      "Training was not queued:",
      store.scopePayload,
      store.selectedPreset,
      err,
    );
  }
}

/**
 * Enter in an Advanced field finishes the edit; it never starts a run. The
 * dialog's Enter-accepts contract skips an event already handled, so this
 * marks it. Buttons in the table keep Enter for themselves.
 */
function keepEnterInField(event) {
  if (event.target instanceof HTMLInputElement) event.preventDefault();
}

// Recipe updates. The first check asks whether to check every day; the answer
// is the setting in Settings › AI-toolkit, so it is asked only once.
const askAutoCheck = ref(false);
const autoCheckNote = ref("");

async function onCheckPresets() {
  if (store.presetCheckBusy) return;
  const firstTime = store.presetsAutoCheck === null;
  await store.checkPresetUpdates();
  if (firstTime && store.presetsAutoCheck === null) askAutoCheck.value = true;
}

async function answerAutoCheck(on) {
  askAutoCheck.value = false;
  try {
    await store.setPresetsAutoCheck(on);
    autoCheckNote.value = on
      ? "PixlStash will check once a day when you open this dialog."
      : "You can turn on automatic checks any time in Settings › AI-toolkit.";
  } catch (err) {
    console.warn("Could not save the recipe update setting:", err);
    autoCheckNote.value = "Could not save that. Try again in Settings › AI-toolkit.";
  }
}

watch(
  () => store.trainDialogOpen,
  (open) => {
    if (!open) {
      askAutoCheck.value = false;
      autoCheckNote.value = "";
    }
  },
);

function goToSettings() {
  store.closeDialog();
  // Bump the deep-link request. App.vue opens the settings dialog and
  // UserSettingsDialog selects the Training tab (both watch this counter).
  store.requestTrainingSettings();
}
</script>

<style scoped>
.tld-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

/* FieldLabel carries its own bottom margin; in a row with a chip the row
   owns the spacing instead. */
.tld-label-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.tld-label-row :deep(.field-label) {
  margin-bottom: 0;
  flex: 1;
}

.tld-g2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-5) var(--space-4);
}

.tld-hint {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.tld-readout {
  display: block;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: rgb(var(--v-theme-on-surface));
}

.tld-readout--quiet {
  color: inherit;
}

.tld-mono {
  font-family: var(--font-mono);
}

.tld-sp {
  flex: 1;
}

/* The character lead: what the entry point already said. */
.tld-lead {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  gap: var(--space-2) var(--space-4);
  align-items: center;
  padding: var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
}

.tld-lead__img {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
  object-fit: cover;
  object-position: top center;
}

.tld-lead__text b {
  display: block;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
}

.tld-lead__text small,
.tld-opt small {
  display: block;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* A Previews card's prices: its time, then its VRAM peak (#2415). */
.tld-price {
  display: block;
  margin-top: var(--space-3);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
}

.tld-price em {
  font-style: normal;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.tld-price--next {
  margin-top: 0;
}

.tld-opt small.tld-price--tight {
  color: rgb(var(--v-theme-surface-warning));
}

.tld-lead__src {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-top: var(--space-2);
}

.tld-lead__src > :last-child {
  flex: 1;
  min-width: 0;
}

.tld-inline-label {
  font-size: var(--text-xs);
  white-space: nowrap;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* Subject cards: a radiogroup of three outcomes, chosen by meaning. */
.tld-opts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
}

.tld-opt {
  position: relative;
  display: flex;
  gap: var(--space-4);
  align-items: flex-start;
  padding: var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font: inherit;
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.tld-opt:not(.tld-opt--on):hover {
  background: var(--hover-wash);
}

.tld-opt b {
  display: block;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.tld-opt__icon {
  margin-top: var(--space-1);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.tld-opt--on {
  border-color: transparent;
  background: var(--active-wash);
}

/* Train on: people and sets as compact radios, three across, the list
   scrolling inside the dialog rather than pushing the rest off it. */
.tld-sources {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  /* Three rows, then it scrolls. */
  max-height: calc(
    3 * (var(--control-h-bar) + var(--space-4)) + 2 * var(--space-3) +
      2 * var(--space-1)
  );
  overflow-y: auto;
  padding: var(--space-1);
}

.tld-source {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  height: calc(var(--control-h-bar) + var(--space-4));
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.tld-source:not(.tld-opt--on):hover {
  background: var(--hover-wash);
}

.tld-source.tld-opt--on::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  box-shadow: inset 0 0 0 2px var(--active-bar);
  pointer-events: none;
}

.tld-source__img {
  flex-shrink: 0;
  width: var(--space-7);
  height: var(--space-7);
  border-radius: var(--radius-sm);
  object-fit: cover;
  object-position: top center;
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.tld-source__img--blank {
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.tld-source__img--person {
  border-radius: var(--radius-pill);
}

.tld-source__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.tld-source__name {
  overflow: hidden;
  font-size: var(--text-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tld-source__text small {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The ring is drawn on an overlay: App.css's
   `button:focus:not(:focus-visible) { box-shadow: none !important }` would
   drop it from the card itself the moment it is clicked. */
.tld-opt--on::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  box-shadow: inset 0 0 0 2px var(--active-bar);
  pointer-events: none;
}

.tld-opt--on .tld-opt__icon {
  color: var(--selected-ink);
}

/* Notices: a status glyph and text, never color alone. */
.tld-notice {
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

.tld-notice--warn .v-icon {
  color: rgb(var(--v-theme-surface-warning));
}

.tld-notice--bad .v-icon {
  color: rgb(var(--v-theme-surface-error));
}

.tld-checks {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.tld-notice--ask {
  align-items: center;
}

.tld-keep__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

/* The quoted caption, its shared phrase marked in the warning hue. */
.tld-keep__cap {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  overflow-wrap: anywhere;
}

.tld-keep__cap mark {
  background: color-mix(in srgb, rgb(var(--v-theme-surface-warning)) 22%, transparent);
  color: rgb(var(--v-theme-on-surface));
  border-radius: var(--space-1);
  padding: 0 var(--space-1);
}

.tld-keep__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.tld-keep__row .v-switch {
  flex: none;
}

.tld-keep__label {
  display: flex;
  flex-direction: column;
}

/* A control prompt's role (a neutral count badge) and what it should do. */
.tld-prompt-role {
  grid-column: 2 / -1;
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.tld-role {
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, rgb(var(--v-theme-on-surface)) 12%, transparent);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  line-height: var(--badge-size);
}

.tld-link {
  background: none;
  border: none;
  padding: 0;
  color: inherit;
  text-decoration: underline;
  cursor: pointer;
  font: inherit;
}

/* Tier 2 */
.tld-more {
  border-top: 1px solid rgb(var(--v-theme-divider));
  padding-top: var(--space-4);
}

.tld-more__toggle {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  min-height: var(--control-h);
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font: inherit;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  cursor: pointer;
}

.tld-more__caret {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  transition: transform var(--dur-1) var(--ease-standard);
}

.tld-more__caret--open {
  transform: rotate(90deg);
}

.tld-more__note {
  font-size: var(--text-xs);
  font-weight: var(--weight-regular);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.tld-more__body {
  padding-top: var(--space-4);
}

.tld-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-sm);
}

.tld-table thead th {
  text-align: left;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.tld-table tbody th,
.tld-table td {
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  vertical-align: middle;
  text-align: left;
  font-weight: var(--weight-regular);
}

.tld-table thead th:nth-child(2),
.tld-table td:nth-child(2) {
  width: 240px;
}

.tld-table tr:has(+ .tld-prompts-row) > * {
  border-bottom: 0;
}

.tld-row--pinned > * {
  background: var(--active-wash);
}

.tld-row--pinned > th {
  box-shadow: inset 3px 0 0 var(--active-bar);
}

.tld-from {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.tld-from__inner {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.tld-cell {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.tld-cell--pair > * {
  flex: 1 1 0;
  min-width: 0;
}

.tld-quiet {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.tld-reset {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  cursor: pointer;
}

.tld-reset:hover {
  background: var(--hover-wash);
}

.tld-sizes {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.tld-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid rgb(var(--v-theme-border));
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-xs);
  white-space: nowrap;
  cursor: pointer;
}

.tld-chip:hover {
  background: var(--hover-wash);
}

.tld-chip--on {
  background: var(--active-wash);
  border-color: var(--active-bar);
  color: rgb(var(--v-theme-on-surface));
}

.tld-chip--word {
  font-family: var(--font-mono);
}

.tld-chip--word:not(.tld-chip--on) span {
  text-decoration: line-through;
}

.tld-prompts-row td {
  padding-top: 0;
}

.tld-prompt {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr) var(--control-h);
  gap: var(--space-3);
  align-items: center;
  margin-bottom: var(--space-2);
}

.tld-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h);
  height: var(--control-h);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: pointer;
}

.tld-remove:hover:not(:disabled) {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.tld-remove:disabled {
  cursor: default;
}

.tld-prompts-note {
  display: block;
  padding-top: var(--space-2);
}

.tld-more-rows {
  padding-left: 0;
}

.tld-foot {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.tld-foot--bad .v-icon {
  color: rgb(var(--v-theme-surface-error));
}
</style>
