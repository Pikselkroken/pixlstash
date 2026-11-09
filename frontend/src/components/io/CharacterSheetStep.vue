<template>
  <div class="cs-split">
    <div class="cs-pane" data-testid="cs-main">
      <template v-if="phase === 'describe'">
        <div class="cs-step-h">
          <h3>Describe {{ name }} and draw sheets</h3>
          <span
            >Each sheet is one person drawn six times: four passport photos
            (front, three-quarter smiling, profile, three-quarter laughing) and
            two body photos. You pick the sheet that looks right.</span
          >
        </div>

        <p v-if="likenessNotice" class="cs-notice" data-testid="cs-likeness">
          <v-icon size="16">{{
            chosenLora ? "mdi-account-check-outline" : "mdi-alert-outline"
          }}</v-icon>
          <span>{{ likenessNotice }}</span>
        </p>
        <p
          v-if="workflowsLoaded && !workflowOptions.length"
          class="cs-notice cs-notice--bad"
          data-testid="cs-no-workflow"
        >
          <v-icon size="16">mdi-alert-circle-outline</v-icon>
          <span
            >Sheets need a MOSS-Image text-to-image workflow, and the library
            has none. Import one from Workflows, then come back.</span
          >
        </p>

        <div class="cs-lora">
          <AppSelect
            v-model="loraSha"
            label="Character LoRA · optional"
            :options="loraOptions"
            data-testid="cs-lora"
          />
          <div v-if="chosenLora" class="cs-sl">
            <span class="cs-sl__lbl">Strength</span>
            <input
              v-model.number="strength"
              class="cs-range"
              type="range"
              min="0.1"
              max="1.5"
              step="0.05"
              aria-label="LoRA strength"
            />
            <span class="cs-sl__val">{{ strength.toFixed(2) }}</span>
          </div>
          <span v-if="chosenLora && trigger" class="cs-hint"
            >Loaded into the sheet workflow, with the trigger word
            <b>{{ trigger }}</b> added to every sheet prompt. Only LoRAs for
            MOSS-Image, the sheet workflow's model, can be chosen.</span
          >
          <span v-else-if="chosenLora" class="cs-hint"
            >Loaded into the sheet workflow. Only LoRAs for MOSS-Image, the
            sheet workflow's model, can be chosen.</span
          >
          <span v-else class="cs-hint"
            >Without a LoRA, each sheet is a slightly different person, so you
            pick one.</span
          >
        </div>

        <div class="cs-fld">
          <AppTextarea
            v-model="form.description"
            label="Description"
            :rows="2"
            placeholder="Adult, hair, eyes, anything that makes the face theirs"
            data-testid="cs-description"
          />
          <span class="cs-hint">{{ descriptionHint }}</span>
        </div>
        <div class="cs-fld">
          <AppInput
            v-model="form.body"
            label="Body"
            placeholder="slim build · curvy with full hips · tall and athletic"
          />
          <span class="cs-hint"
            >Goes into every photo, the passport photos too: a different build
            draws a different face. Empty is an average build.</span
          >
        </div>
        <div class="cs-grid2">
          <AppInput
            v-model="form.top"
            label="Top"
            placeholder="a plain oatmeal crew-neck sweater"
          />
          <AppInput
            v-model="form.trousers"
            label="Trousers"
            placeholder="dark straight jeans"
          />
        </div>
        <AppInput
          v-if="form.framing === 'full_body'"
          v-model="form.shoes"
          label="Shoes"
          placeholder="white sneakers"
        />
        <div class="cs-grid2">
          <div class="cs-fld">
            <FieldLabel>Body photos</FieldLabel>
            <Segmented
              v-model="form.framing"
              aria-label="Body photos"
              :options="FRAMINGS"
              data-testid="cs-framing"
              @pick="framingTouched = true"
            />
            <span class="cs-hint" data-testid="cs-framing-hint">{{
              framingHint
            }}</span>
          </div>
          <div class="cs-fld">
            <FieldLabel>Sheets to draw</FieldLabel>
            <Segmented
              v-model="form.count"
              aria-label="Sheets to draw"
              :options="COUNTS"
            />
            <span class="cs-hint">About a minute each.</span>
          </div>
        </div>
        <div class="cs-fld">
          <FieldLabel>Draw a</FieldLabel>
          <Segmented
            v-model="form.subject"
            aria-label="Draw a"
            :options="SUBJECTS"
          />
        </div>
        <div v-if="workflowOptions.length" class="cs-wfline">
          <AppSelect
            v-model="workflowId"
            label="Sheet workflow"
            :options="workflowOptions"
            compact
          />
        </div>
      </template>

      <template v-else>
        <div class="cs-step-h">
          <h3>Pick a sheet</h3>
          <span>{{ pickIntro }}</span>
        </div>
        <p
          v-if="noneHold"
          class="cs-notice cs-notice--warn"
          data-testid="cs-none-hold"
        >
          <v-icon size="16">mdi-alert-outline</v-icon>
          <span
            >None of the {{ ended.length }} sheets shows one person in all six
            photos. Drawing more usually finds one. A shorter description with
            fewer competing details helps too.</span
          >
        </p>
        <p
          v-if="failures.length"
          class="cs-notice cs-notice--bad"
          data-testid="cs-failed"
        >
          <v-icon size="16">mdi-alert-circle-outline</v-icon>
          <span
            >{{ failures.length }} of the runs failed: {{ failures[0] }}</span
          >
        </p>
        <ul class="cs-sheets">
          <li v-for="sheet in shownSheets" :key="sheet.picture_id">
            <button
              type="button"
              class="cs-sheetimg"
              :class="{ 'cs-sheetimg--on': sheet.picture_id === chosen }"
              :aria-pressed="sheet.picture_id === chosen"
              :aria-label="`Sheet ${sheet.n}: ${sheetWords(sheet)}`"
              :disabled="
                (!sheet.faces_read && !gaveUpOnFaces) ||
                working ||
                phase === 'filing'
              "
              data-testid="cs-sheet"
              @click="chosen = sheet.picture_id"
            >
              <img
                :src="pictureThumbnailUrl(sheet.picture_id)"
                alt=""
                loading="lazy"
              />
              <span class="cs-sheetimg__rk">{{ sheet.n }}</span>
              <span
                v-if="sheet.picture_id === chosen"
                class="cs-sheetimg__ck"
                aria-hidden="true"
                ><v-icon size="14">mdi-check</v-icon></span
              >
            </button>
            <span class="cs-cap">
              <b>Sheet {{ sheet.n }}</b> · {{ sheetWords(sheet) }}
            </span>
          </li>
          <li v-for="slot in pendingSlots" :key="slot.key">
            <span class="cs-sheetimg cs-sheetimg--wait">{{ slot.label }}</span>
          </li>
        </ul>
      </template>
    </div>

    <aside class="cs-pane cs-pane--side" data-testid="cs-rail">
      <h3 class="cs-secl">{{ name }} so far</h3>
      <div class="cs-rail">
        <div v-if="chosenSheet" class="cs-item">
          <img
            class="cs-rail__sheet"
            :src="pictureThumbnailUrl(chosenSheet.picture_id)"
            alt=""
          />
          <span
            ><b>Sheet {{ chosenSheet.n }}</b> ·
            {{ chosenSheet.agreement_words }}</span
          >
        </div>
        <div v-if="referenceIds.length" class="cs-item">
          <span class="cs-faces">
            <img
              v-for="id in referenceIds"
              :key="id"
              :src="pictureThumbnailUrl(id)"
              alt=""
            />
          </span>
          <span
            ><b>{{ name }}'s reference faces</b>. New sheets are ranked by how
            close they come to these.</span
          >
        </div>
        <div class="cs-item">
          <span class="cs-row1"
            ><b>Description</b>
            <button
              v-if="phase !== 'describe'"
              type="button"
              class="cs-link"
              :disabled="working || phase === 'filing'"
              @click="phase = 'describe'"
            >
              Edit
            </button></span
          >
          <span class="cs-desc">{{
            form.description.trim() || "No description yet."
          }}</span>
        </div>
      </div>
      <div class="cs-gapbox">
        <h4 class="cs-secl">Still to come</h4>
        <div class="cs-todo">
          <span v-if="phase === 'describe'"
            ><span class="cs-n">1</span
            ><span
              ><b>Sheets</b> · 2–4 sheets of six photos, pick one</span
            ></span
          >
          <span
            ><span class="cs-n">2</span
            ><span
              ><b>Pick the set</b> · new and existing pictures, ranked
              together</span
            ></span
          >
        </div>
      </div>
    </aside>
  </div>
</template>

<script setup>
/**
 * Step 1 of "Create a training set…" when the set is drawn rather than picked.
 *
 * Draws 2-4 character sheets with a MOSS-Image workflow (an ordinary
 * `POST /workflows/run`, so it queues and shows in Tasks like any run), ranks
 * them by how alike each sheet's own faces are, and cuts the one the user picks
 * into six pictures on the character. Emits `done` with their ids, and the
 * dialog's picker takes over. The dialog owns the footer: it reads `footer`
 * and calls the actions this component exposes.
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import {
  buildSheetPrompt,
  rankSheets,
  useSheet,
} from "../../api/characterSheets";
import {
  getCharacter,
  getReferencePictures,
  patchCharacter,
} from "../../api/characters";
import { listAdapters } from "../../api/modelShelf";
import { pictureThumbnailUrl } from "../../api/pictures";
import {
  getRunOutcome,
  getWorkflowCard,
  listWorkflowCards,
  runWorkflowCard,
} from "../../api/workflows";
import { errorMessage } from "../../utils/apiError";
import { runKey } from "../../utils/gpuMemory";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import Segmented from "../widgets/Segmented.vue";

const props = defineProps({
  /** { id, name } of the character. */
  character: { type: Object, required: true },
  /** How many pictures the character already has. */
  pictureCount: { type: Number, default: 0 },
});

const emit = defineEmits(["done"]);

const FRAMINGS = [
  { id: "mid_thigh", label: "Mid-thigh" },
  { id: "knees_up", label: "Knees-up" },
  { id: "full_body", label: "Full body" },
];
const COUNTS = [2, 3, 4].map((n) => ({ id: n, label: String(n) }));
const SUBJECTS = [
  { id: "woman", label: "Woman" },
  { id: "man", label: "Man" },
];
const MOSS = "moss_image";
const POLL_MS = 2500;
// Panels are filed before their faces are read; the picker ranks by face, so
// it waits for them, but not forever: a slow queue still hands over.
const FACE_WAIT_MS = 90_000;
// A sheet whose faces are still unread this long after its runs ended (no face
// model, a failed extraction, a deleted picture) stops being waited for: it
// can be chosen unscored, and the polling stops.
const SHEET_FACE_WAIT_MS = 120_000;
const HEIGHT_WORDS = /\b(tall|short|petite|height|legs?|leggy|long-legged)\b/i;

const name = computed(() => props.character?.name || "");
const phase = ref("describe"); // describe | drawing | pick | filing
const form = reactive({
  description: "",
  body: "",
  top: "",
  trousers: "",
  shoes: "",
  framing: "mid_thigh",
  count: 4,
  subject: "woman",
});
const framingTouched = ref(false);
const savedDescription = ref("");
const loras = ref([]);
const loraSha = ref("");
const strength = ref(0.8);
const workflows = ref([]);
const workflowsLoaded = ref(false);
const workflowId = ref("");
const referenceIds = ref([]);
const runs = ref([]); // { key, status, pictureIds, message }
const ranking = ref({}); // picture id -> RankedSheet
const order = ref([]); // sheet picture ids in the order they arrived
const chosen = ref(null);
const working = ref(false);
const error = ref("");
// Drawing waits for the character's stored description, so a typed one never
// overwrites it.
const loaded = ref(false);
const gaveUpOnFaces = ref(false);
let timer = null;
let ticking = false;
let runsEndedAt = 0;
let alive = true;

const chosenLora = computed(
  () => loras.value.find((lora) => lora.sha256 === loraSha.value) || null,
);
const trigger = computed(() => {
  const words = chosenLora.value?.trigger_words;
  return (Array.isArray(words) ? words[0] : words) || "";
});
const loraName = computed(() =>
  chosenLora.value
    ? chosenLora.value.display_name || chosenLora.value.filename
    : "",
);

const loraOptions = computed(() => [
  { value: "", label: "No LoRA" },
  ...loras.value.map((lora) => {
    const fits = lora.base_model_family === MOSS;
    return {
      value: lora.sha256,
      label: lora.display_name || lora.filename,
      disabled: !fits,
      chips: [
        fits
          ? lora.base_model || "MOSS-Image"
          : `For ${lora.base_model || "another model"}; sheets run MOSS-Image`,
      ],
    };
  }),
]);

const workflowOptions = computed(() =>
  workflows.value.map((card) => ({ value: card.id, label: card.name })),
);

const suggestedFraming = computed(() =>
  chosenLora.value ? "full_body" : "mid_thigh",
);

watch(suggestedFraming, (framing) => {
  if (!framingTouched.value) form.framing = framing;
});

const framingHint = computed(() => {
  const reason = chosenLora.value
    ? "Suggested: full body. The LoRA holds the face, so the photos can show the whole body."
    : "Suggested: mid-thigh. With no LoRA the sheet alone carries the face, so faces need the most pixels.";
  const height =
    form.framing !== "full_body" && HEIGHT_WORDS.test(form.body)
      ? " This framing won't show height."
      : "";
  return reason + height;
});

const descriptionHint = computed(() => {
  if (savedDescription.value) {
    return `From ${name.value}. Changes here apply to these sheets only.`;
  }
  return `${name.value} has no description yet. This one is saved as theirs when the first sheets are drawn.`;
});

const likenessNotice = computed(() => {
  if (chosenLora.value) {
    return `Drawn with ${loraName.value}, a LoRA of ${name.value}, so the sheets start from that likeness. Without a LoRA they would be a new likeness drawn from words.`;
  }
  if (props.pictureCount > 0) {
    return `These sheets are drawn from words, not from ${name.value}'s ${props.pictureCount} pictures. What they make is a new likeness, filed on ${name.value} and ranked beside the pictures already there.`;
  }
  return "";
});

const ended = computed(() =>
  order.value.filter((id) => ranking.value[id]?.faces_read),
);

function score(sheet) {
  return (sheet.agreement ?? -1) + (sheet.closeness ?? 0);
}

/** Every sheet, numbered by arrival, best first; broken ones last. */
const sheets = computed(() => {
  const numbered = order.value.map((id, index) => ({
    picture_id: id,
    n: index + 1,
    faces_read: false,
    ...(ranking.value[id] || {}),
  }));
  return numbered.sort(
    (a, b) =>
      Number(!!a.broken) - Number(!!b.broken) ||
      Number(b.faces_read) - Number(a.faces_read) ||
      score(b) - score(a),
  );
});

const holding = computed(() =>
  sheets.value.filter(
    (s) => s.faces_read && !s.broken && s.agreement_words !== "faces don't hold",
  ),
);

const runsEnded = computed(() =>
  runs.value.every((run) => ["completed", "failed"].includes(run.status)),
);

const allEnded = computed(
  () =>
    runsEnded.value &&
    (gaveUpOnFaces.value ||
      order.value.every((id) => ranking.value[id]?.faces_read)),
);

const noneHold = computed(
  () => allEnded.value && ended.value.length > 0 && !holding.value.length,
);

// Broken sheets are set aside, and shown only when nothing else is.
const shownSheets = computed(() => {
  const unbroken = sheets.value.filter((s) => !s.broken);
  return unbroken.length || !allEnded.value ? unbroken : sheets.value;
});

const failures = computed(() =>
  runs.value
    .filter((run) => run.status === "failed")
    .map((run) => run.message || "the run failed."),
);

const pendingSlots = computed(() =>
  runs.value
    .filter((run) => !["completed", "failed"].includes(run.status))
    .map((run, index) => ({
      key: run.key,
      label: run.status === "running" && index === 0 ? "drawing" : "queued",
    })),
);

const chosenSheet = computed(
  () => sheets.value.find((s) => s.picture_id === chosen.value) || null,
);

const pickIntro = computed(() => {
  const base =
    "Sheets are ranked by how well their own faces agree. The six photos of the one you choose become pictures of " +
    name.value +
    "; the others stay in the library.";
  if (!referenceIds.value.length) return base;
  return `${base} Each is also compared with ${name.value}'s reference faces: a sheet that agrees with itself but drifts from them is a different person drawn well.`;
});

function sheetWords(sheet) {
  if (!sheet.faces_read) {
    return gaveUpOnFaces.value ? "faces not read" : "reading faces…";
  }
  if (sheet.broken) return "more than one person in a photo";
  const words = [sheet.agreement_words];
  if (sheet.close === true) words.push(`close to ${name.value}`);
  if (sheet.close === false) words.push(`drifts from ${name.value}`);
  return words.join(" · ");
}

function stopPolling() {
  if (timer) clearTimeout(timer);
  timer = null;
}

function schedule() {
  // A tick in flight schedules the next one itself when it ends.
  if (ticking || timer || !alive) return;
  timer = setTimeout(tick, POLL_MS);
}

async function followRuns() {
  for (const run of runs.value) {
    if (["completed", "failed"].includes(run.status)) continue;
    try {
      const outcome = await getRunOutcome(run.key);
      run.status = outcome.status;
      run.message = outcome.message || "";
      for (const id of outcome.picture_ids || []) {
        if (!order.value.includes(id)) order.value.push(id);
      }
    } catch (e) {
      if (e?.response?.status === 404) {
        run.status = "failed";
        run.message = "PixlStash lost track of this run. Check Tasks for it.";
      } else {
        console.warn("Couldn't read a sheet run", run.key, e);
      }
    }
  }
}

async function tick() {
  timer = null;
  ticking = true;
  try {
    await followRuns();
    if (order.value.length) {
      const answer = await rankSheets(props.character.id, order.value);
      ranking.value = Object.fromEntries(
        answer.sheets.map((sheet) => [sheet.picture_id, sheet]),
      );
    }
  } catch (e) {
    console.warn("Couldn't rank the character sheets", e);
  } finally {
    ticking = false;
  }
  if (!alive) return;
  if (!runsEnded.value) runsEndedAt = 0;
  else if (!runsEndedAt) runsEndedAt = Date.now();
  if (runsEndedAt && Date.now() - runsEndedAt > SHEET_FACE_WAIT_MS) {
    gaveUpOnFaces.value = true;
  }
  if (!chosen.value && holding.value.length) {
    chosen.value = holding.value[0].picture_id;
  }
  if (allEnded.value) {
    if (phase.value === "drawing") phase.value = "pick";
    return;
  }
  schedule();
}

async function draw() {
  if (working.value || !canDraw.value) return;
  working.value = true;
  error.value = "";
  try {
    const description = form.description.trim();
    if (!savedDescription.value && description) {
      await patchCharacter(props.character.id, { description });
      savedDescription.value = description;
    }
    const sheet = await buildSheetPrompt({
      description,
      body: form.body,
      top: form.top,
      trousers: form.trousers,
      shoes: form.shoes,
      framing: form.framing,
      subject: form.subject,
      trigger: trigger.value,
    });
    const { card } = await getWorkflowCard(workflowId.value);
    const sizes = (card?.defaults || []).filter((d) =>
      ["width", "height"].includes(d.input_name),
    );
    if (!sizes.length) {
      throw new Error(
        `${card?.name || "The workflow"} has no picture size to set, so it can't draw a sheet.`,
      );
    }
    const answer = await runWorkflowCard({
      workflow_id: workflowId.value,
      prompt: sheet.prompt,
      negative: sheet.negative,
      values: sizes.map((d) => ({
        slot_label: d.slot_label,
        input_name: d.input_name,
        value: d.input_name === "width" ? sheet.width : sheet.height,
      })),
      add_loras: chosenLora.value
        ? [
            {
              sha256: chosenLora.value.sha256,
              strength_model: strength.value,
              strength_clip: strength.value,
            },
          ]
        : [],
      count: form.count,
      // A sheet is six photos of one person: none of its faces is anybody's
      // until it is chosen and cut, so nothing here names the character.
      character_ids: [],
      label: `Character sheet · ${name.value}`,
    });
    const started = (answer?.prompts || []).filter((p) => runKey(p));
    if (!started.length) {
      throw new Error(
        answer?.error ||
          "The workflow can't run as it is. Open it from Workflows to see why.",
      );
    }
    runs.value.push(
      ...started.map((p) => ({
        key: runKey(p),
        status: p.status === "failed" ? "failed" : "queued",
        message: "",
      })),
    );
    phase.value = "drawing";
    gaveUpOnFaces.value = false;
    schedule();
  } catch (e) {
    error.value = `Couldn't start the sheets. ${errorMessage(e, "Please try again.")}`;
  } finally {
    working.value = false;
  }
}

async function use() {
  if (working.value || !chosen.value) return;
  working.value = true;
  error.value = "";
  phase.value = "filing";
  let ids;
  try {
    ({ picture_ids: ids } = await useSheet(props.character.id, chosen.value));
  } catch (e) {
    phase.value = "pick";
    error.value = `Couldn't use the sheet. ${errorMessage(e, "Please try again.")}`;
    working.value = false;
    return;
  }
  // The sheet is cut and filed now; only its faces may still be unread. A
  // failure from here on is no reason to offer the cut again.
  const deadline = Date.now() + FACE_WAIT_MS;
  while (alive && Date.now() < deadline) {
    try {
      const answer = await rankSheets(props.character.id, ids);
      if (answer.sheets.every((sheet) => sheet.faces_read)) break;
    } catch (e) {
      console.warn("Couldn't read the new pictures' faces", ids, e);
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
  working.value = false;
  if (!alive) return;
  // Back on the sheets for a return through the step row, none chosen: this
  // one is used.
  phase.value = "pick";
  chosen.value = null;
  emit("done", ids);
}

const canDraw = computed(
  () =>
    loaded.value &&
    !!workflowId.value &&
    !!form.description.trim() &&
    phase.value !== "filing",
);

const footer = computed(() => {
  const n = form.count;
  if (phase.value === "describe") {
    return {
      lead: `Pictures are filed on ${name.value}`,
      back: order.value.length
        ? { label: "Back to the sheets", run: () => (phase.value = "pick") }
        : null,
      primary: {
        label: `Draw ${n} ${n === 1 ? "sheet" : "sheets"}`,
        icon: "draw",
        enabled: canDraw.value,
        run: draw,
      },
    };
  }
  if (phase.value === "filing") {
    return {
      lead: "Cutting the sheet into six pictures and reading their faces…",
      primary: { label: "Use this sheet", enabled: false, run: use },
    };
  }
  const waiting = pendingSlots.value.length;
  return {
    lead: waiting
      ? `Drawing ${waiting} more, about a minute each. Closing this leaves them drawing in Tasks; they land in the library.`
      : chosenSheet.value
        ? `Sheet ${chosenSheet.value.n} becomes ${name.value}'s pictures.`
        : "",
    // The rail's Edit goes back to the form too; the footer offers it only
    // when no sheet is worth using.
    back: noneHold.value
      ? { label: "Edit the description", run: () => (phase.value = "describe") }
      : null,
    secondary: {
      label: `Draw ${n} more`,
      enabled: canDraw.value && !working.value,
      run: draw,
    },
    primary: {
      label: chosenSheet.value ? `Use sheet ${chosenSheet.value.n}` : "Use a sheet",
      enabled:
        !!chosenSheet.value &&
        (chosenSheet.value.faces_read || gaveUpOnFaces.value) &&
        !working.value,
      run: use,
    },
  };
});

async function load() {
  const id = props.character.id;
  const [character, adapters, cards, references] = await Promise.allSettled([
    getCharacter(id),
    listAdapters({ characterId: id }),
    listWorkflowCards(),
    getReferencePictures(id),
  ]);
  if (!alive) return;
  if (character.status === "fulfilled") {
    savedDescription.value = (character.value?.description || "").trim();
    if (!form.description) form.description = savedDescription.value;
  }
  if (adapters.status === "fulfilled") {
    loras.value = [...adapters.value].sort((a, b) =>
      String(b.added_at || "").localeCompare(String(a.added_at || "")),
    );
    // The newest LoRA that loads into the sheet workflow is preselected.
    loraSha.value =
      loras.value.find((lora) => lora.base_model_family === MOSS)?.sha256 || "";
  }
  if (cards.status === "fulfilled") {
    workflows.value = cards.value.cards
      .filter(
        (card) =>
          card.type === "txt2img" &&
          (card.models || []).some((m) => m.base_model_family === MOSS),
      )
      .sort((a, b) =>
        String(b.last_used || "").localeCompare(String(a.last_used || "")),
      );
    workflowId.value = workflows.value[0]?.id || "";
  }
  workflowsLoaded.value = true;
  if (references.status === "fulfilled") {
    referenceIds.value = (references.value?.reference_picture_ids || []).slice(
      0,
      4,
    );
  }
  loaded.value = character.status === "fulfilled";
  if (!loaded.value) {
    error.value = `Couldn't read ${name.value}. Close and open the dialog to try again.`;
  }
  for (const result of [character, adapters, cards, references]) {
    if (result.status === "rejected") {
      console.warn("Couldn't load part of the sheet form", result.reason);
    }
  }
}

onMounted(load);
onBeforeUnmount(() => {
  alive = false;
  stopPolling();
});

defineExpose({ footer, error, working, loraName, phase });
</script>

<style scoped>
.cs-split {
  flex: 1;
  min-height: 0;
  display: flex;
}

.cs-pane {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.cs-pane--side {
  flex: 0 0 280px;
  gap: var(--space-4);
  border-left: 1px solid rgb(var(--v-theme-divider));
}

.cs-step-h {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.cs-step-h h3 {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.cs-step-h span,
.cs-hint {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-hint {
  font-size: var(--text-2xs);
}

.cs-hint b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

.cs-fld {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.cs-grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
}

.cs-lora {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.cs-sl {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.cs-sl__lbl {
  min-width: 56px;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-range {
  flex: 1;
  margin: 0;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}

.cs-sl__val {
  min-width: 4ch;
  text-align: right;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}

.cs-wfline {
  max-width: 360px;
}

.cs-notice {
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

.cs-notice .v-icon {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-notice--warn .v-icon {
  color: rgb(var(--v-theme-surface-warning));
}

.cs-notice--bad .v-icon {
  color: rgb(var(--v-theme-surface-error));
}

.cs-sheets {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-4) var(--space-3);
}

.cs-sheets li {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.cs-sheetimg {
  position: relative;
  display: block;
  width: 100%;
  aspect-ratio: 5 / 3;
  padding: 0;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
  cursor: pointer;
}

.cs-sheetimg:disabled {
  cursor: default;
}

.cs-sheetimg img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.cs-sheetimg--on {
  box-shadow: 0 0 0 2px var(--active-bar);
}

.cs-sheetimg--wait {
  display: grid;
  place-items: center;
  border: 1px dashed rgb(var(--v-theme-border));
  background: transparent;
  cursor: default;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-sheetimg__rk,
.cs-sheetimg__ck {
  position: absolute;
  top: var(--space-2);
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
}

.cs-sheetimg__rk {
  left: var(--space-2);
  min-width: 18px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
}

.cs-sheetimg__ck {
  right: var(--space-2);
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

.cs-cap {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-cap b,
.cs-item b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

.cs-secl {
  margin: 0;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-rail {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.cs-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-row1 {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}

.cs-link {
  margin-left: auto;
  padding: 0;
  border: 1px solid transparent;
  background: none;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
  cursor: pointer;
}

.cs-desc {
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-surface));
}

.cs-rail__sheet {
  width: 100%;
  aspect-ratio: 5 / 3;
  object-fit: cover;
  border-radius: var(--radius-md);
}

.cs-faces {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-1);
}

.cs-faces img {
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
  border-radius: var(--radius-sm);
}

.cs-gapbox {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.cs-todo {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-xs);
}

.cs-todo > span {
  display: flex;
  gap: var(--space-2);
  align-items: baseline;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-todo b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

.cs-n {
  min-width: 12px;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
}
</style>
