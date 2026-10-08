<template>
  <!-- PixlStash's check, the owner's question and the suspect notes for one set,
       as the note rows both set trays draw (docs/ideas/workflow-set-verdicts.md).
       The server decides every state and every suspect; this only words them. -->
  <div
    v-if="copy || $slots.plain"
    class="msp__note"
    role="row"
    aria-level="2"
    data-testid="model-set-note"
  >
    <div role="gridcell">
      <v-icon size="16">mdi-information-outline</v-icon>
      <span class="svn__body">
        <template v-if="copy?.count">
          <span data-testid="model-set-count">{{ copy.count }}</span>
          <template v-if="copy.ask">
            {{ " " }}<span data-testid="model-set-ask">{{ copy.ask }}</span>
            <template v-if="copy.caveat">
              {{ " " }}<span>{{ copy.caveat }}</span></template
            >
          </template>
        </template>
        <template v-else>
          <slot name="plain" />
          <template v-if="copy?.unchecked"
            >{{ " " }}{{ copy.unchecked }}</template
          >
        </template>
        <VerdictAsk
          v-if="copy?.ask"
          class="svn__ask"
          :verdict="check.verdict"
          :options="SET_OPTIONS"
          :answered-text="check.verdict ? setVerdictText(check.verdict) : ''"
          group-label="Does this set produce sensible output?"
          :error="setError"
          @answer="answerSet"
          @cancel="setError = ''"
        />
      </span>
    </div>
  </div>

  <!-- Tab-reachable way to change an answer about a model: the row and card
       icons are off the tab order because the grid's cursor owns Tab. -->
  <div
    v-if="answered.length"
    class="msp__note"
    role="row"
    aria-level="2"
    data-testid="model-set-answers"
  >
    <div role="gridcell">
      <v-icon size="16">mdi-information-outline</v-icon>
      <span class="svn__body">
        <span>Your answers about models in this set:</span>
        <span
          v-for="item in answered"
          :key="item.id"
          class="svn__answer"
          data-testid="model-set-answer"
        >
          <VerdictIcon
            interactive
            :data-verdict-line="item.id"
            :verdict="item.verdict"
            :label="item.label"
            @click="reopenMember(item.id, 'line')"
          />
          <span aria-hidden="true">{{ item.name }}</span>
        </span>
      </span>
    </div>
  </div>

  <div
    v-for="note in notes"
    :key="note.model_id"
    class="msp__note"
    role="row"
    aria-level="2"
    data-testid="model-set-suspect"
  >
    <div role="gridcell">
      <v-icon size="16">mdi-alert-outline</v-icon>
      <span class="svn__body">
        <VerdictAsk
          :ref="(el) => holdAsk(note.model_id, el)"
          :verdict="note.verdict"
          :options="memberOptions(note.model_id)"
          :answered-text="
            note.verdict
              ? memberVerdictText(note.verdict, nameOf(note.model_id))
              : ''
          "
          :group-label="`Is ${nameOf(note.model_id)} a problem?`"
          :error="memberErrors[note.model_id] ?? ''"
          @answer="(verdict) => answerMember(note.model_id, verdict)"
          @cancel="cancelMember(note.model_id)"
        >
          <template v-if="note.suspect">
            <strong>{{ nameOf(note.model_id) }}</strong> may be a problem.
            {{ note.suspect.with_failed }} of {{ note.suspect.with_total }}
            pictures made with it don't look like their prompt, against
            {{ note.suspect.without_failed }} of
            {{ note.suspect.without_total }} without it. Is
            {{ nameOf(note.model_id) }} a problem?
          </template>
          <template v-else>
            Is <strong>{{ nameOf(note.model_id) }}</strong> a problem in this
            set?
          </template>
        </VerdictAsk>
      </span>
    </div>
  </div>
  <div
    v-if="moreSuspects"
    class="msp__note"
    role="row"
    aria-level="2"
    data-testid="model-set-suspects-more"
  >
    <div role="gridcell">
      <v-icon size="16">mdi-alert-outline</v-icon>
      <span
        >{{ moreSuspects }} more
        {{ moreSuspects === 1 ? "model" : "models" }} flagged</span
      >
    </div>
  </div>

  <!-- Mounted before any answer, so the first one is announced. -->
  <div
    class="visually-hidden"
    role="status"
    aria-live="polite"
    data-testid="model-set-live"
  >
    {{ announced }}
  </div>
</template>

<script setup>
import { computed, nextTick, reactive, ref } from "vue";
import { VIcon } from "vuetify/components";

import {
  SAVE_ERROR,
  checkCopy,
  memberVerdictOf,
  memberVerdictText,
  setVerdictText,
} from "../../utils/setVerdicts";
import VerdictAsk from "../widgets/VerdictAsk.vue";
import VerdictIcon from "../widgets/VerdictIcon.vue";

const props = defineProps({
  /** The set's `set_checks` entry, or null (then nothing is drawn). */
  check: { type: Object, default: null },
  /** The set's members as `{id, name}`, to name a model in a note. */
  members: { type: Array, default: () => [] },
  /** `(verdict) => Promise`: record the set's verdict; rejects on failure. */
  saveSet: { type: Function, required: true },
  /** `(modelId, verdict) => Promise`: the same for one member. */
  saveMember: { type: Function, required: true },
});

const SET_OPTIONS = [
  {
    value: "yes",
    label: "Yes",
    name: "Yes, this set produces sensible output",
  },
  {
    value: "no",
    label: "No",
    name: "No, this set does not produce sensible output",
  },
];

// Max 3 suspect notes; the rest are counted, never drawn.
const SUSPECT_NOTES = 3;

const copy = computed(() => checkCopy(props.check));
const suspects = computed(() => props.check?.suspects ?? []);
const moreSuspects = computed(() =>
  Math.max(suspects.value.length - SUSPECT_NOTES, 0),
);

// A member the owner reopened from its row, when it has no suspect note.
const extraId = ref(null);

const notes = computed(() => {
  const shown = suspects.value.slice(0, SUSPECT_NOTES).map((s) => ({
    model_id: s.model_id,
    verdict: s.verdict,
    suspect: s,
  }));
  if (
    extraId.value == null ||
    shown.some((n) => n.model_id === extraId.value)
  ) {
    return shown;
  }
  return [
    ...shown,
    {
      model_id: extraId.value,
      verdict: memberVerdictOf(props.check, extraId.value),
      suspect: null,
    },
  ];
});

// One entry per answered member that is in this set, in the payload's order.
const answered = computed(() =>
  (props.check?.member_verdicts ?? [])
    .filter((m) => props.members.some((x) => x.id === m.model_id))
    .map((m) => ({
      id: m.model_id,
      verdict: m.verdict,
      name: nameOf(m.model_id),
      label: `${nameOf(m.model_id)}: your verdict, ${
        m.verdict === "problem" ? "a problem" : "not a problem"
      } in this set. Change`,
    })),
);

function nameOf(id) {
  return props.members.find((m) => m.id === id)?.name ?? `model ${id}`;
}

function memberOptions(id) {
  const name = nameOf(id);
  return [
    {
      value: "problem",
      label: "Yes",
      name: `Yes, ${name} is a problem in this set`,
    },
    {
      value: "not_problem",
      label: "No",
      name: `No, ${name} is not a problem in this set`,
    },
  ];
}

// What the live region says after an answer. Cleared first so repeating the
// same answer is announced again.
const announced = ref("");
function announce(text) {
  announced.value = "";
  queueMicrotask(() => {
    announced.value = text;
  });
}

const setError = ref("");
const memberErrors = reactive({});

async function answerSet(verdict) {
  setError.value = "";
  try {
    await props.saveSet(verdict);
    announce(`Recorded: ${setVerdictText(verdict)}`);
  } catch {
    // The caller has logged it with its context; the owner sees this.
    setError.value = SAVE_ERROR;
    announce(SAVE_ERROR);
  }
}

async function answerMember(id, verdict) {
  delete memberErrors[id];
  try {
    await props.saveMember(id, verdict);
    announce(`Recorded: ${memberVerdictText(verdict, nameOf(id))}`);
  } catch {
    memberErrors[id] = SAVE_ERROR;
    announce(SAVE_ERROR);
  }
}

const asks = new Map();
function holdAsk(id, el) {
  if (el) asks.set(id, el);
  else asks.delete(id);
}

function cancelMember(id) {
  delete memberErrors[id];
  if (id === extraId.value) extraId.value = null;
  else if (opener !== "line") return;
  // Hand focus back to the icon that opened the question. A timeout, so it
  // lands after the question's own focus on its collapsed icon.
  const attr = opener === "line" ? "data-verdict-line" : "data-verdict-member";
  setTimeout(() => document.querySelector(`button[${attr}="${id}"]`)?.focus());
}

// Which icon opened the extra note, so Cancel can return to that one.
let opener = "member";

/** A member's icon was pressed: reopen its question, suspect or not. */
async function reopenMember(id, from = "member") {
  opener = from;
  if (!notes.value.some((n) => n.model_id === id)) {
    extraId.value = id;
    await nextTick();
  }
  asks.get(id)?.reopen();
}

defineExpose({ reopenMember });
</script>

<style scoped>
.msp__note > [role="gridcell"] {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: var(--space-4) var(--space-4) 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-surface-info), 0.14);
  font-size: var(--text-xs);
}

.msp__note [role="gridcell"] > .v-icon {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-info));
}

.svn__body {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-3);
}

.svn__answer {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.svn__ask {
  flex-basis: 100%;
}
</style>
