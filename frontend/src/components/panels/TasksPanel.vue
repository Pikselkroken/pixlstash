<template>
  <!-- Deliberately multi-root: these are the inspector body's own flex
       children, exactly as they were when they lived in `StatsSidebar`. A
       wrapper div would make them one child and change the spacing in a rail
       this change is not supposed to touch. It also means `.tm-system-bar`'s
       `margin-top: auto` still resolves against the body: inert in the stats
       rail, which never gives `.inspector-body` a height, and a real pin to
       the bottom on `/workflows`, where the inspector's footer slot makes the
       body `flex: 1` (`.inspector-content--footed`). That difference is pre-existing and left alone. -->
  <!-- An offer to save what a run changed is not an active task, and the
       panel is not empty with one in it either: the idle line gives way. -->
  <div
    v-if="tasksStore.activeEntries.length === 0 && offers.length === 0"
    class="tm-idle-msg"
  >
    No active tasks
  </div>
  <div v-if="tasksStore.activeEntries.length" class="tm-worker-list">
    <template v-for="entry in tasksStore.activeEntries" :key="entry.key">
      <!-- ComfyUI runs in flight, as the backend reports them: a progress
           bar + abort, which stops them all -->
      <div v-if="entry.kind === 'comfyui'" class="tm-worker-row">
        <div class="tm-worker-row-top">
          <span class="tm-status-dot tm-status-dot--running"></span>
          <span class="tm-worker-label">{{ entry.run.label }}</span>
          <button
            class="tm-comfy-abort"
            type="button"
            aria-label="Abort ComfyUI run"
            @click="tasksStore.abortComfyuiRuns()"
          >
            <Tooltip
              text="Abort ComfyUI run"
              activator="parent"
              :describe="false"
            />
            ✕
          </button>
        </div>
        <div class="tm-comfy-bar">
          <div class="tm-comfy-fill" :style="{ width: barWidth(entry.run) }"></div>
        </div>
        <div class="tm-comfy-message">{{ entry.run.message }}</div>
      </div>
      <!-- A model downloading for a workflow: its bytes as a bar, and a
           cancel that discards what it had fetched. -->
      <div
        v-else-if="entry.kind === 'download'"
        class="tm-worker-row"
        data-testid="tm-download-row"
      >
        <div class="tm-worker-row-top">
          <span class="tm-status-dot tm-status-dot--running"></span>
          <span class="tm-worker-label">{{ entry.run.label }}</span>
          <button
            v-if="!isReadOnly"
            class="tm-comfy-abort"
            type="button"
            :aria-label="`Cancel downloading ${entry.run.label}`"
            data-testid="tm-download-cancel"
            @click="tasksStore.cancelDownload(entry.id)"
          >
            <Tooltip
              text="Cancel download"
              activator="parent"
              :describe="false"
            />
            ✕
          </button>
        </div>
        <div class="tm-comfy-bar">
          <div class="tm-comfy-fill" :style="{ width: barWidth(entry.run) }"></div>
        </div>
        <div class="tm-comfy-message">{{ entry.run.message }}</div>
      </div>
      <!-- Async import (#459): the two-phase dialog auto-hides at the safe
             transition and the import lands here as a determinate task row.
             data-import-task-row is the FLIP flight target the import dialog
             flies its count chip into. -->
      <div
        v-else-if="entry.kind === 'import'"
        class="tm-worker-row tm-import-row"
        :data-import-task-row="entry.key"
      >
        <div class="tm-worker-row-top">
          <span
            class="tm-status-dot"
            :class="{
              'tm-status-dot--running': entry.run.status === 'running',
            }"
          ></span>
          <span class="tm-worker-label">{{ entry.run.label }}</span>
          <span v-if="entry.run.total > 0" class="tm-worker-progress">
            {{ entry.run.current }} / {{ entry.run.total }}
          </span>
          <!-- Cancel is offered only while the import is genuinely
                 client-abortable (the pre-commit upload window, run.abortable).
                 A committed server-side import cannot be stopped from the
                 client, so no cancel control is shown for it. -->
          <button
            v-if="entry.run.abortable && !isReadOnly"
            class="tm-comfy-abort"
            type="button"
            aria-label="Cancel import"
            @click="tasksStore.abortImportRun(entry.key)"
          >
            <Tooltip
              text="Cancel import"
              activator="parent"
              :describe="false"
            />
            ✕
          </button>
        </div>
        <div class="tm-comfy-bar">
          <div class="tm-comfy-fill" :style="{ width: barWidth(entry.run) }"></div>
        </div>
        <div class="tm-comfy-message">{{ entry.run.message }}</div>
      </div>
      <!-- Backend worker: throughput sparkline + rate -->
      <div v-else class="tm-worker-row">
        <div class="tm-worker-row-top">
          <span
            class="tm-status-dot"
            :class="{
              'tm-status-dot--running':
                entry.snapshot.running || tmGetLatestRate(entry.key) > 0,
            }"
          ></span>
          <span class="tm-worker-label">{{
            tmFormatLabel(entry.key, entry.snapshot.label)
          }}</span>
          <span class="tm-worker-progress">{{
            tmFormatProgress(entry.snapshot)
          }}</span>
        </div>
        <div class="tm-worker-row-bottom">
          <canvas
            :ref="(el) => tmRegisterCanvas(entry.key, el)"
            class="tm-sparkline"
          ></canvas>
          <span class="tm-worker-rate">
            {{ tmFormatRate(tmGetLatestRate(entry.key)) }}/s
          </span>
        </div>
      </div>
    </template>
  </div>
  <!-- What the last run of a workflow changed (#1850): the Run popup's two
       verbs again, once the pictures are back. Rows of the same list, not a
       toast: pictures get rated before this is decided, so the offer stays
       until that workflow runs again or the changes are saved. -->
  <div
    v-if="offers.length"
    ref="offersList"
    class="tm-worker-list"
    data-testid="tm-offers"
  >
    <div
      v-for="offer in offers"
      :key="offer.workflowId"
      class="tm-worker-row tm-offer"
      role="group"
      :aria-label="`Changes the last run of ${offer.name} was made with`"
      :data-offer="offer.workflowId"
    >
      <div class="tm-worker-row-top">
        <span class="tm-worker-label">{{ offer.name }}</span>
      </div>
      <p class="tm-offer-text">
        Ran with <b>{{ changesWord(offer.count) }} from the workflow</b
        ><template v-if="offer.text">: {{ offer.text }}</template
        ><template v-else>.</template>
      </p>
      <!-- Save as new… is the row's one question: the name. One write is out
           at a time, and while it is every verb of every row says so. The
           field is `readonly` for it, not `disabled`: Enter leaves the
           keyboard in it, a disabled field cannot hold the keyboard, and a
           refusal has to find it there. -->
      <template v-if="naming === offer.workflowId">
        <AppInput
          v-model="newName"
          label="Name of the new workflow"
          :readonly="Boolean(busy)"
          :class="{ 'tm-offer-refused': Boolean(busy) }"
          @enter="saveAsNew(offer)"
          @keydown.esc="backFromName(offer)"
          @keydown.stop
        />
        <div class="tm-offer-acts">
          <AppButton
            variant="ghost"
            size="sm"
            :disabled="Boolean(busy)"
            @click="backFromName(offer)"
          >
            Back
          </AppButton>
          <AppButton
            size="sm"
            :loading="busy === `new:${offer.workflowId}`"
            :disabled="Boolean(busy)"
            :class="{ 'tm-offer-refused': !newName.trim() }"
            :aria-disabled="newName.trim() ? undefined : 'true'"
            @click="saveAsNew(offer)"
          >
            Save as new workflow
          </AppButton>
        </div>
      </template>
      <div v-else class="tm-offer-acts">
        <AppButton
          data-as-new
          size="sm"
          :disabled="Boolean(busy)"
          @click="askName(offer)"
        >
          Save as new…
        </AppButton>
        <!-- `aria-disabled`, not `disabled`: a closed Save stays reachable,
             with the server's reason as its description. -->
        <AppButton
          size="sm"
          icon-left="content-save-outline"
          :loading="busy === `save:${offer.workflowId}`"
          :disabled="Boolean(busy)"
          :class="{ 'tm-offer-refused': Boolean(offer.closed) }"
          :aria-disabled="offer.closed ? 'true' : undefined"
          :aria-describedby="offer.closed ? `${whyId}-${offer.workflowId}` : undefined"
          @click="save(offer)"
        >
          Save
        </AppButton>
      </div>
      <p
        v-if="offer.closed && naming !== offer.workflowId"
        :id="`${whyId}-${offer.workflowId}`"
        class="tm-offer-why"
      >
        {{ offer.closed }}
      </p>
      <p v-if="errors[offer.workflowId]" class="tm-offer-why tm-offer-bad" role="alert">
        {{ errors[offer.workflowId] }}
      </p>
    </div>
  </div>
  <!-- What the last save here did, and where focus goes when the row and its
       buttons leave with the offer: read on landing there, so it is no live
       region as well. It stands until an offer arrives after it. What the
       save left of the Workflow tab's changes is inside it, so it is read
       with it and goes with it: two lines to the eye, and a space between
       them for whatever reads the text. -->
  <p
    v-if="doneNote"
    ref="doneLine"
    class="tm-offer-done"
    tabindex="-1"
    data-testid="tm-offer-done"
  >
    {{ doneNote }}
    <span v-if="doneLost" class="tm-offer-lost">{{ doneLost }}</span
    >{{ " " }}<span v-if="doneWaits">{{ doneWaits }}</span>
  </p>
  <div v-if="tmSystemItems.length" class="tm-system-bar">
    <!-- The figures are the server process's own, not the machine's. -->
    <div class="tm-system-heading section-label">
      <span class="tm-system-mark" aria-hidden="true"></span>
      PixlStash resource usage
    </div>
    <div v-for="item in tmSystemItems" :key="item.label" class="tm-system-item">
      <span class="tm-system-label">{{ item.label }}</span>
      <span class="tm-system-value">{{ item.value }}</span>
    </div>
  </div>
</template>

<script>
/**
 * The tab that shows this panel, so its word and its glyph are declared once
 * for every inspector that offers it. `tasksTabFor` lights it up while work is
 * running, and is what a host imports.
 */
const TASKS_TAB = {
  value: "tasks",
  label: "Tasks",
  icon: "mdi-timeline-clock-outline",
};

/**
 * The descriptor an inspector declares for its Tasks tab, lit while the store
 * has work. One function so the two hosts cannot drift apart on the wording or
 * on when the light comes on.
 */
export function tasksTabFor(tasksStore) {
  if (!tasksStore.hasActiveTasks) return TASKS_TAB;
  const n = tasksStore.activeCount;
  return {
    ...TASKS_TAB,
    busy: true,
    busyTooltip: `${n} active task${n === 1 ? "" : "s"}`,
  };
}
</script>

<script setup>
// The task manager: the body of the Tasks tab, wherever an inspector offers it.
//
// It started inside StatsSidebar and is a component of its own because the
// Workflows view's inspector offers the same tab: somebody browsing
// workflows or recipes has to be able to watch the work their last run started
// without leaving the screen to do it. The image overlay is the one inspector
// that does not offer it - it is a picture's pane, laid over the picture.
//
// The data (worker snapshots, rate series, active-state, ComfyUI runs) lives in
// the shared tasks store, which is the single poller of /workers/progress. This
// component owns only the view: canvas sparkline drawing and the label / number
// formatting - and, while it is mounted, the store's fast poll cadence.

import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  useId,
  watch,
} from "vue";

import {
  getWorkflowChanges,
  saveWorkflow,
  saveWorkflowAsNew,
} from "../../api/workflows";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { isReadOnly } from "../../utils/apiClient";
import {
  changesWord,
  describeChanges,
  saveFailure,
  suggestedName,
} from "../../utils/workflowChanges";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import Tooltip from "../widgets/Tooltip.vue";

const tasksStore = useTasksStore();

// ── Save what a run changed (#1850) ────────────────────────────────────────
//
// Whether there is an offer is the server's to say: `GET …/changes` answers
// `ran`, the set the workflow's last run was made with, until it runs again,
// is saved, or gets another version. The Run popup leaves a record of the run
// in `useRunDialogStore` (the band's own words, and when its pictures are
// back), which is what an offer is WORDED from and when it appears; the
// record is never the reason one is drawn. After a reload the record is gone
// and the server's `ran` is not, so the one workflow selected on the
// Workflows screen is asked about too, and offered with a count alone.

const runDialog = useRunDialogStore();
const workflows = useWorkflowsStore();
const whyId = useId();

/** The workflows there may be an offer for. None in a read-only session. */
const candidates = computed(() => {
  if (isReadOnly.value) return [];
  const ids = Object.keys(runDialog.ranChanges).filter(
    (id) => runDialog.ranChanges[id].finished,
  );
  const selected =
    workflows.selectedKeys.length === 1 ? workflows.selectedKeys[0] : "";
  // One whose run is on record and still out is not offered yet.
  if (selected && !(selected in runDialog.ranChanges)) ids.push(selected);
  return ids;
});
/** workflow id -> what the server answered, for the ones it has a `ran` for. */
const confirmed = ref({});
let confirmAsk = 0;

/** Ask the server about every candidate; only the newest asking is kept. */
async function confirmOffers() {
  const ask = ++confirmAsk;
  const ids = candidates.value;
  const answers = await Promise.all(
    ids.map((id) =>
      getWorkflowChanges(id).catch((err) => {
        console.warn(`[tasks] could not read what the last run of ${id} changed`, err);
        return null;
      }),
    ),
  );
  if (ask !== confirmAsk) return;
  confirmed.value = Object.fromEntries(
    ids.map((id, at) => [id, answers[at]]).filter(([, state]) => state?.ran),
  );
}

// Asked before anything is drawn, and again whenever a workflow may have
// changed: a save made in this tab (`saved`), and the grid being read again,
// which is how a save from another tab or from ComfyUI arrives here.
watch(
  [() => candidates.value.join("\n"), () => runDialog.saved, () => workflows.cards],
  confirmOffers,
  { immediate: true },
);

const offers = computed(() =>
  candidates.value.flatMap((id) => {
    const state = confirmed.value[id];
    if (!state) return [];
    const record = runDialog.ranChanges[id];
    const card = workflows.cards.find((entry) => entry.id === id);
    return [
      {
        workflowId: id,
        name: record?.name || card?.name || "This workflow",
        // The band's own count and sentence where this tab saw the run; a
        // count alone otherwise, since the labels were the popup's.
        count: record?.count ?? describeChanges(state.ran).count,
        text: record?.text || "",
        ran: state.ran,
        version: state.version ?? null,
        // The server's sentence for a closed Save, printed as sent.
        closed:
          state.ran_save && !state.ran_save.open
            ? state.ran_save.reason || "Save is not open for these changes."
            : "",
      },
    ];
  }),
);

/** `save:<id>` or `new:<id>` while that write is out, else "". */
const busy = ref("");
/** workflow id -> why its last save here did not land. */
const errors = reactive({});
/** The workflow whose row is asking for a name, or "". */
const naming = ref("");
const newName = ref("");
const offersList = ref(null);
/** What the last save here did, or "". */
const doneNote = ref("");
const doneLine = ref(null);
// A save here writes what the run used. A change made in the Workflow tab
// after the run is not part of it: the server keeps it waiting
// (`still_waiting`), or drops it where the saved workflow can no longer take
// it (`not_kept`), and says which once, in the save's answer. `still_waiting`
// also counts a waiting set that was already stale, which the save leaves
// untouched and which was not necessarily made after the run, so its sentence
// says only that they wait and were not part of this save. Both are said
// in the receipt, inside the line that takes focus, so they are read with it
// and stand as long as it does. Not a notice while there is a line: a card
// is announced politely just as focus moves here and holds one sentence,
// clamped to three lines, and what was not kept needs its names and reason.
const doneLost = ref("");
const doneWaits = ref("");

/** A control of one offer's row: found by the `data-offer` the row carries. */
function inOffer(workflowId, selector) {
  const row = [...(offersList.value?.children ?? [])].find(
    (el) => el.dataset.offer === String(workflowId),
  );
  return row?.querySelector(selector) ?? null;
}

// The line is the receipt of the last save made here, and is taken down as
// the Run popup's is: when there is something to save again. An offer that
// arrives after it is news the line would sit beside as if it were current.
// One that was on screen before it is not, and a later save writes its own
// line over this one.
watch(
  () => offers.value.map((offer) => offer.workflowId),
  async (now, before) => {
    const arrived = now.find((id) => !before.includes(id));
    if (!arrived || !doneNote.value) return;
    // Taken down from under the keyboard, it hands focus to what took its place.
    const held = document.activeElement === doneLine.value;
    doneNote.value = "";
    if (!held) return;
    await nextTick();
    inOffer(arrived, "[data-as-new]")?.focus();
  },
);

/** A save landed: the offer goes, and so must not take the keyboard with it. */
async function landed(offer, note, saved, savedAs = "") {
  doneNote.value = note;
  // Named as the offer names changes, without the popup's word for a
  // parameter, which left with the popup: what cannot be kept is a LoRA or a
  // model, and neither needs one.
  const lost = describeChanges(saved?.not_kept);
  doneLost.value = lost.text
    ? `Could not keep ${changesWord(lost.count)} made after the run: ${lost.text} The saved workflow no longer has what was changed, so this is gone: not saved, and not left waiting.`
    : "";
  // They wait on the workflow the offer was for, which Save as new left alone.
  const waits = Number(saved?.still_waiting) || 0;
  doneWaits.value = waits
    ? `${offer.name} still has ${changesWord(waits)} waiting in the Workflow tab. They were not part of this save.`
    : "";
  // Its own name question goes with it; another row's is still being answered.
  if (naming.value === offer.workflowId) naming.value = "";
  const next = { ...confirmed.value };
  delete next[offer.workflowId];
  confirmed.value = next;
  runDialog.changesSaved(offer.workflowId, savedAs);
  workflows.invalidate();
  await nextTick();
  doneLine.value?.focus?.();
  // No line to say it on: the tab was closed while the save was out. What was
  // not kept is said this once and shows nowhere else, so a notice carries
  // it, and stays until it is dismissed.
  if (!doneLine.value && doneLost.value) {
    useNoticeStore().warning(`${note} ${doneLost.value}`, { timeout: 0 });
  }
}

/** Save: what that run changed, written over the workflow as its next version. */
async function save(offer) {
  if (busy.value || offer.closed) return;
  busy.value = `save:${offer.workflowId}`;
  delete errors[offer.workflowId];
  try {
    const saved = await saveWorkflow(offer.workflowId, {
      changes: offer.ran,
      version: offer.version,
      from_run: true,
    });
    await landed(offer, `Saved to ${offer.name}.`, saved);
  } catch (err) {
    errors[offer.workflowId] = saveFailure(err);
  } finally {
    busy.value = "";
  }
}

async function askName(offer) {
  if (busy.value) return;
  delete errors[offer.workflowId];
  newName.value = suggestedName(offer.name);
  naming.value = offer.workflowId;
  await nextTick();
  const field = inOffer(offer.workflowId, "input");
  field?.focus();
  field?.select();
}

async function backFromName(offer) {
  // Escape reaches here from a field that is only read-only while a write is
  // out: it waits as the Back button does.
  if (busy.value) return;
  naming.value = "";
  await nextTick();
  inOffer(offer.workflowId, "[data-as-new]")?.focus();
}

/** Save as new: the same changes stored as a new workflow; this one is left alone. */
async function saveAsNew(offer) {
  const name = newName.value.trim();
  if (busy.value || !name) return;
  busy.value = `new:${offer.workflowId}`;
  delete errors[offer.workflowId];
  try {
    const saved = await saveWorkflowAsNew(offer.workflowId, {
      name,
      changes: offer.ran,
      from_run: true,
    });
    await landed(
      offer,
      `Saved as ${saved?.name || name}.`,
      saved,
      saved?.workflow_id || "",
    );
  } catch (err) {
    errors[offer.workflowId] = saveFailure(err);
  } finally {
    busy.value = "";
  }
}

const TM_WINDOW_SECONDS = 120; // sparkline x-axis span, seconds
const tmCanvasRefs = new Map(); // key → { el, observer }

// Mounted is exactly "the Tasks tab is on screen": the inspector unmounts its
// body when the rail is collapsed, so the fast cadence follows what is actually
// visible rather than which tab was last picked. The store keeps polling either
// way (that's what drives the app-wide activity light); this only changes how
// often.
onMounted(() => tasksStore.setTasksTabOpen(true));

onUnmounted(() => {
  tasksStore.setTasksTabOpen(false);
  for (const { observer } of tmCanvasRefs.values()) observer.disconnect();
  tmCanvasRefs.clear();
});

function tmRegisterCanvas(key, el) {
  const existing = tmCanvasRefs.get(key);
  if (existing) {
    existing.observer.disconnect();
    tmCanvasRefs.delete(key);
  }
  if (!el) return;
  const observer = new ResizeObserver(() => {
    tmDrawSparkline(el, tasksStore.series[key] || []);
  });
  observer.observe(el);
  tmCanvasRefs.set(key, { el, observer });
  requestAnimationFrame(() =>
    tmDrawSparkline(el, tasksStore.series[key] || []),
  );
}

function tmGetThemeRgb(name) {
  if (typeof window === "undefined") return null;
  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue(`--v-theme-${name}`)
      .trim() || null
  );
}

function tmThemeRgba(name, alpha, fallback = "0,0,0") {
  const v = tmGetThemeRgb(name) || fallback;
  return `rgba(${v}, ${alpha})`;
}

function tmFormatRate(value) {
  const rate = Number(value || 0);
  if (rate >= 10) return rate.toFixed(0);
  if (rate >= 1) return rate.toFixed(1);
  return rate.toFixed(2);
}

function tmDrawAll() {
  for (const [key, { el }] of tmCanvasRefs.entries()) {
    tmDrawSparkline(el, tasksStore.series[key] || []);
  }
}

// The store reassigns its `series` ref on every poll; redraw the sparklines
// whenever it does. tmDrawAll is a no-op when no canvases are mounted (no
// backend worker running), so this is cheap to leave always-on.
watch(
  () => tasksStore.series,
  () => requestAnimationFrame(tmDrawAll),
);

function tmDrawSparkline(canvas, samples) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(
    4,
    Math.floor(rect.width || canvas.parentElement?.clientWidth || 200),
  );
  const height = Math.max(4, Math.floor(rect.height || 28));
  const tw = Math.floor(width * dpr);
  const th = Math.floor(height * dpr);
  if (canvas.width !== tw || canvas.height !== th) {
    canvas.width = tw;
    canvas.height = th;
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);

  const plotSamples = samples.length
    ? samples
    : [{ rate: 0, t: Date.now() / 1000 }];
  const maxRate = Math.max(1, ...plotSamples.map((s) => s.rate || 0));
  const pad = 2;
  const plotW = width - pad * 2;
  const plotH = height - pad * 2;
  const tNow = Date.now() / 1000;
  const tMin = tNow - TM_WINDOW_SECONDS;
  const tRange = tNow - tMin;
  const tToX = (t) => pad + ((t - tMin) / tRange) * plotW;

  ctx.beginPath();
  plotSamples.forEach((s, i) => {
    const x = tToX(s.t ?? tNow);
    const y = pad + plotH * (1 - (s.rate || 0) / maxRate);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = tmThemeRgba("tertiary", 0.85, "142,166,4");
  ctx.lineWidth = 1.5;
  ctx.stroke();

  const last = plotSamples[plotSamples.length - 1];
  ctx.lineTo(tToX(last.t ?? tNow), pad + plotH);
  ctx.lineTo(pad, pad + plotH);
  ctx.closePath();
  ctx.fillStyle = tmThemeRgba("tertiary", 0.18, "142,166,4");
  ctx.fill();
}

const tmLabelMap = {
  quality_scored: "Quality",
  pictures_tagged: "Tags",
  descriptions_generated: "Descriptions",
  text_embeddings: "Text embeddings",
  image_embeddings: "Image embeddings",
  faces_extracted: "Faces extracted",
  likeness_pairs: "Likeness pairs",
  likeness_parameters: "Likeness params",
  watch_folder_import: "Folder import",
  comfyui_extraction: "ComfyUI backfill",
  tag_predictions_scored: "Tag Predictions",
  missing_file_purge: "File cleanup",
  snapshot_identity_scrub: "Snapshot cleanup",
  planner_managed: "Planner task",
  checkpoints_hashed: "Checkpoint Hash",
  workflows_carded: "Workflow cards",
  text_score: "Text score",
  text_read: "Text in pictures",
  object_detection: "Object detection",
};

const tmWorkerLabelMap = {
  ReferenceFolderScanTask: "Reference folder scan",
  SourceFaceLikenessTask: "Source face likeness",
  SmartScoreTask: "Smart score",
};

function tmToTitleWords(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (char) => char.toUpperCase())
    .trim();
}

function tmFallbackWorkerLabel(key) {
  if (tmWorkerLabelMap[key]) return tmWorkerLabelMap[key];
  return tmToTitleWords(String(key || "").replace(/(Worker|Task)$/, ""));
}

function tmFormatLabel(key, label) {
  if (tmLabelMap[label]) {
    if (label === "planner_managed") return tmFallbackWorkerLabel(key);
    return tmLabelMap[label];
  }
  if (label && label !== "idle" && label !== "uninitialized") {
    return tmToTitleWords(label);
  }
  return tmFallbackWorkerLabel(key);
}

/** A ComfyUI run, download or import's bar fill, as a CSS width clamped to 0-100%. */
function barWidth(run) {
  return `${Math.min(100, Math.max(0, Math.round(run.percent)))}%`;
}

function tmFormatProgress(snapshot) {
  const current = Number(snapshot?.current || 0);
  const total = Number(snapshot?.total || 0);
  if (!total) return `${current}`;
  return `${current} / ${total}`;
}

function tmFormatPercent(value) {
  const percent = Number(value);
  if (!Number.isFinite(percent)) return "n/a";
  if (percent >= 10) return `${percent.toFixed(0)}%`;
  return `${percent.toFixed(1)}%`;
}

function tmFormatGigabytes(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "n/a";
  return `${amount.toFixed(1)} GB`;
}

function tmFormatUsage(used, total, percent) {
  const usedLabel = tmFormatGigabytes(used);
  if (Number.isFinite(total)) {
    return `${usedLabel} / ${tmFormatGigabytes(total)} (${tmFormatPercent(percent)})`;
  }
  const percentLabel = tmFormatPercent(percent);
  if (percentLabel !== "n/a") return `${usedLabel} (${percentLabel})`;
  return usedLabel;
}

function tmGetLatestRate(key) {
  return tasksStore.getLatestRate(key);
}

const tmSystemItems = computed(() => {
  const usage = tasksStore.systemUsage || {};
  const items = [];
  const cpuAllCores = Number.isFinite(usage.cpu_percent_all_cores)
    ? usage.cpu_percent_all_cores
    : usage.cpu_percent;
  if (Number.isFinite(cpuAllCores)) {
    items.push({ label: "CPU", value: tmFormatPercent(cpuAllCores) });
  }
  if (Number.isFinite(usage.ram_used_gb)) {
    items.push({
      label: "RAM",
      value: tmFormatUsage(
        usage.ram_used_gb,
        usage.ram_total_gb,
        usage.ram_percent,
      ),
    });
  }
  items.push({
    label: "VRAM",
    value: Number.isFinite(usage.vram_used_gb)
      ? tmFormatUsage(
          usage.vram_used_gb,
          usage.vram_total_gb,
          usage.vram_percent,
        )
      : "n/a",
  });
  return items;
});
</script>

<style scoped>
.tm-idle-msg {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.4);
  font-style: italic;
  padding: var(--space-4) 0 var(--space-2);
}

.tm-worker-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  width: 100%;
}

.tm-worker-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.04);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.07);
  min-width: 0;
}

.tm-worker-row-top {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.tm-worker-row-bottom {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.tm-sparkline {
  flex: 1;
  height: 28px;
  min-width: 0;
  display: block;
  border-radius: var(--radius-sm);
}

.tm-worker-rate {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.45);
  white-space: nowrap;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  min-width: 38px;
  text-align: right;
}

.tm-status-dot {
  /* Sized as the attention dot (visual-language §12); amber only while running. */
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  border-radius: var(--radius-pill);
  flex-shrink: 0;
  background: rgba(var(--v-theme-on-surface), 0.2);
}

.tm-status-dot--running {
  background: rgb(var(--v-theme-accent));
  box-shadow: 0 0 5px rgba(var(--v-theme-accent), 0.55);
  animation: tm-dot-pulse 1.4s ease-in-out infinite;
}

/* ── ComfyUI run rows ──────────────────────────────────────────────────────── */
.tm-comfy-bar {
  height: 6px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.12);
  overflow: hidden;
}

.tm-comfy-fill {
  height: 100%;
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-primary));
  transition: width 0.25s ease;
}

.tm-comfy-message {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.5);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tm-comfy-abort {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: var(--text-xs);
  line-height: 1;
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
}
.tm-comfy-abort:hover {
  color: rgb(var(--v-theme-surface-error));
  background: rgba(var(--v-theme-error), 0.12);
}

@keyframes tm-dot-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.45;
    transform: scale(0.78);
  }
}

@media (prefers-reduced-motion: reduce) {
  .tm-status-dot--running {
    animation: none;
  }
}

/* Landing punctuation for the async import (#459): a one-shot spring-pop + accent
   glow-and-settle when the FLIP flight from the import dialog lands the chip on
   this row (mirrors the gridNewPulse landing-pulse pattern, visual-language §10).
   One-shot `both` so it plays once on the row's first render, then rests. */
.tm-import-row {
  animation:
    tm-import-pop var(--dur-4) var(--ease-spring) both,
    tm-import-glow 2.2s ease-out both;
}

@keyframes tm-import-pop {
  0% {
    transform: scale(0.92);
  }
  100% {
    transform: scale(1);
  }
}

@keyframes tm-import-glow {
  0% {
    box-shadow: 0 0 0 0 rgba(var(--v-theme-accent), 0.5);
    border-color: rgba(var(--v-theme-accent), 0.6);
  }
  35% {
    box-shadow: 0 0 0 4px rgba(var(--v-theme-accent), 0.18);
    border-color: rgba(var(--v-theme-accent), 0.45);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(var(--v-theme-accent), 0);
    border-color: rgba(var(--v-theme-on-surface), 0.07);
  }
}

@media (prefers-reduced-motion: reduce) {
  .tm-import-row {
    animation: none;
  }
}

.tm-worker-label {
  flex: 1;
  font-size: var(--text-2xs);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: rgba(var(--v-theme-on-surface), 0.85);
}

.tm-worker-progress {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.5);
  white-space: nowrap;
  flex-shrink: 0;
}

/* ── What a run changed, offered for saving ────────────────────────────────── */
.tm-offer {
  gap: var(--space-3);
  padding: var(--space-3);
}
.tm-offer-text,
.tm-offer-why,
.tm-offer-done {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.tm-offer-text b {
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-surface));
}
.tm-offer-acts {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}
/* A closed verb stays reachable (`aria-disabled`), and so does the name field
   while a write is out (`readonly`), so each is dimmed by hand, as far as a
   natively disabled button is. */
.tm-offer-refused {
  opacity: var(--opacity-disabled);
}
.tm-offer-bad {
  color: rgb(var(--v-theme-surface-error));
}
.tm-offer-done {
  padding: var(--space-2) 0;
}
/* What the save left, each on a line of its own under the receipt. */
.tm-offer-done span {
  display: block;
  margin-top: var(--space-1);
}
/* The one outcome that loses something. */
.tm-offer-lost {
  color: rgb(var(--v-theme-surface-warning));
}
.tm-system-bar {
  margin-top: auto;
  padding-top: var(--space-3);
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: 100%;
}

.tm-system-heading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* The logo as a silhouette in the label's own ink: a quiet mark in chrome,
   not the amber brand moment. */
.tm-system-mark {
  width: 1.2em;
  height: 1.2em;
  flex-shrink: 0;
  background-color: currentColor;
  -webkit-mask: url("/Logo.png") center / contain no-repeat;
  mask: url("/Logo.png") center / contain no-repeat;
}

.tm-system-item {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-3);
  font-size: var(--text-2xs);
}

.tm-system-label {
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-transform: uppercase;
  font-size: var(--text-2xs);
  flex-shrink: 0;
}

.tm-system-value {
  color: rgba(var(--v-theme-on-surface), 0.75);
  text-align: right;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
