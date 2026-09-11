<script setup>
/**
 * Wizard step 3 ("Preview") - the accepted mapping, before anything is
 * written. Commits nothing until "Yes, build this library" is pressed; see
 * integration_architecture.md §22. Moves, renames and copies zero files
 * either way - committing registers the folder for in-place indexing and
 * writes database rows only.
 */
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";

import {
  getFolderStructureCommitStatus,
  startFolderStructureCommit,
  stopFolderStructureCommit,
} from "../../api/folderStructure";
import { errorDetail } from "../../utils/apiError";
import { FACET_KINDS, kindStyle } from "../../utils/folderMappingKinds";
import AppButton from "../widgets/AppButton.vue";
import AppSelect from "../widgets/AppSelect.vue";

const props = defineProps({
  path: { type: String, required: true },
  readTaskId: { type: String, required: true },
  /**
   * The read's own result, for a commit whose task no longer exists. The
   * desktop's first run reads the folder on one server process and restarts
   * onto the GPU runtime before the owner answers, so the task is gone and the
   * result is all there is. Ignored whenever `readTaskId` is set.
   */
  readResult: { type: Object, default: null },
  assignments: { type: Array, required: true },
  /**
   * The owner's saved answers for the read's caption-file patterns, for a
   * resumed commit: `[{suffix, kind}]`. Seeds the choices below when it
   * names a pattern; otherwise the read's own guess does - except for an
   * explicit `[]`, which is the answer "read nothing" and seeds every row as
   * Ignore rather than handing the guesses back.
   *
   * `null` means nobody was ever asked - a wizard the owner has not answered
   * on yet, or an entry saved before the caption card existed. With a card to
   * show it behaves as no saved answer; with none - the commit-on-mount path -
   * it travels to the commit as `null` and the request leaves the key out, so
   * the import probes as it always did. It is the default because "no answer
   * held" is not the same claim as `[]`, "asked, and there was nothing".
   */
  captions: { type: Array, default: null },
  label: { type: String, default: "" },
  pictureCount: { type: Number, default: 0 },
  // "reference" registers the scanned root as an external reference folder;
  // "local_import" imports its pictures as ordinary managed pictures of the
  // active library instead (v1.11 Phase 3, "Bring them in" on a freshly
  // created library - integration_architecture.md §22).
  mode: { type: String, default: "reference" },
  // Whether the library this commit writes into exists yet. "Add a library"
  // shows this step BEFORE building the library: then "Yes, build this
  // library" and "Organise later" emit `build` with the assignments to send
  // and nothing is committed here. Once the library exists (the wizard is
  // resumed after the switch) they commit directly, as ever.
  libraryExists: { type: Boolean, default: true },
  // Resumed right after the switch that built the library: commit the
  // `assignments` as soon as this step mounts, so the owner lands on the
  // running import rather than on a button they already pressed.
  commitOnMount: { type: Boolean, default: false },
});

const emit = defineEmits([
  "back",
  "build",
  "cancel",
  "committed",
  "commit-started",
  "update:captions",
  "update:committing",
]);

const committing = ref(false);
// The wizard makes its dialog undismissable while this is true: a commit,
// once started, runs to completion server-side regardless of what this
// screen does next (§22), so Escape or a backdrop click must not be able to
// quietly abandon the UI while it keeps running - that is what let the same
// read's task id come back through the sidebar's resume flow and get
// committed a second time.
watch(committing, (value) => emit("update:committing", value));
const commitError = ref("");
const commitTaskId = ref("");
const stage = ref("");
const processed = ref(0);
const total = ref(0);

let pollTimer = null;
let disposed = false;

/**
 * The caption-file conventions the read found beside the pictures, each with
 * the read's guess at what it holds. The owner confirms or corrects per
 * pattern - a wrongly read convention would tag every picture in the library
 * with a description's words, so this is asked, not assumed.
 */
const CAPTION_CHOICES = [
  { value: "tags", label: "Tags" },
  { value: "description", label: "Description" },
  { value: "ignore", label: "Ignore" },
];
/**
 * Only a local import asks. A reference folder's caption files belong to its
 * own editor, and the commit refuses the mode/captions pair with a 400, so
 * neither the card, the counts beside it, nor the payload may carry them.
 */
const patterns = computed(() =>
  props.mode === "local_import" ? (props.readResult?.captions ?? []) : [],
);
/**
 * The read stopped early, so this list is the patterns found so far (§20).
 * The answers still apply tree-wide - they are per suffix, so an answered
 * suffix is read in the folders the walk never reached - but a pattern that
 * lives only there was never offered, and a card that says nothing reads as
 * "these are all your caption files".
 */
const patternsPartial = computed(
  () =>
    props.mode === "local_import" &&
    props.readResult?.captions_complete === false,
);
// suffix -> the owner's answer. Seeded from the saved answers, then the read.
// Null-prototype, because the keys are filename fragments: on a plain object
// `answers.constructor` is already truthy so the seeding skips it and a
// function is committed as the kind, and `answers.__proto__ = "tags"` is
// swallowed by the inherited setter instead of storing the answer.
const answers = reactive(Object.create(null));
watch(
  patterns,
  (rows) => {
    // A saved `[]` is the answer "read nothing" - Organise later, or a commit
    // that failed after it. Falling through to each row's guess lost it the
    // moment the read had patterns to show, and `chosenCaptions` then handed
    // the commit a convention the owner had already declined.
    const readNothing = props.captions?.length === 0;
    for (const row of rows) {
      if (answers[row.suffix]) continue;
      answers[row.suffix] =
        props.captions?.find((c) => c.suffix === row.suffix)?.kind ??
        (readNothing ? "ignore" : row.kind);
    }
  },
  { immediate: true },
);
/** The answers on the card: one per reported pattern, or the saved ones when
 *  the read is gone (a resumed commit with no result to list them from).
 *  Only "Yes, build this library" sends these - see `commit`; the commit on
 *  mount, which is the one path nobody stands at, passes `props.captions`
 *  straight through instead.
 *
 *  With no rows and no saved answer the read decides: a COMPLETE one answers
 *  `[]`, "everything was sniffed and there is nothing to read". A PARTIAL one
 *  answers `null` - the walk stopped before the folders that hold the rest, so
 *  nobody was asked about them and the import probes as it always did (§22).
 *  A saved `[]` is still an answer and stays `[]` either way. */
function chosenCaptions() {
  if (props.mode !== "local_import") return [];
  if (!patterns.value.length)
    return props.captions ?? (patternsPartial.value ? null : []);
  return patterns.value.map((row) => ({
    suffix: row.suffix,
    kind: answers[row.suffix] ?? row.kind,
  }));
}
/**
 * The wizard keeps the answers as they are made, not as the commit is pressed.
 * "Back to the mapping" unmounts this step, so an answer held only here was
 * lost on the way back and the read's own guess was committed instead; the
 * wizard's `captions` re-seeds this step on the way forward again.
 *
 * Only an owner's change reports. Watching `answers` instead reported the
 * seeding too: on a resumed commit-on-mount, whose read result arrives after
 * the commit has already started, the seeding wrote the read's guesses into
 * `answers` and the emit replaced the held `[]`/`null` with them - so a retry
 * after a failed auto-commit sent a convention nobody confirmed.
 */
function answerChosen(suffix, kind) {
  answers[suffix] = kind;
  emit("update:captions", chosenCaptions());
}
/**
 * How many files carry a given answer. Counted per kind, not read against
 * unread, because only `tags` spares the tagger: a picture with a description
 * file still has no tags and is queued for the tagger exactly as an unread
 * one is. Ignore is named beside them so it is seen to cost something.
 */
function captionFiles(kind) {
  return patterns.value.reduce(
    (sum, row) =>
      sum + ((answers[row.suffix] ?? row.kind) === kind ? row.files : 0),
    0,
  );
}
const captionFilesAsTags = computed(() => captionFiles("tags"));
const captionFilesAsDescriptions = computed(() => captionFiles("description"));
const captionFilesIgnored = computed(() => captionFiles("ignore"));

const grouped = computed(() => {
  const byKind = new Map(FACET_KINDS.map((k) => [k.value, new Map()]));
  for (const assignment of props.assignments) {
    const bucket = byKind.get(assignment.kind);
    if (!bucket) continue;
    const name = assignment.relative_path.split("/").pop();
    if (!bucket.has(name)) bucket.set(name, assignment);
  }
  return byKind;
});

// "No folder is created inside your library" is the reference-folder framing:
// the scanned root stays external and the library's own directory is
// untouched. For a local import the scanned root already IS the library's own
// root (that is the only case the server allows it), so the fact worth
// stating instead is what these pictures become - ordinary library pictures,
// not an external reference folder the owner could later "stop using".
const lastFact = computed(() =>
  props.mode === "local_import"
    ? "these pictures become ordinary pictures of this library, not an external reference folder"
    : "no folder is created inside your library",
);

/**
 * How many entities the commit creates or matches.
 *
 * The sum of the per-kind buckets, NOT a set of names across them: `grouped`
 * has already collapsed same-named folders WITHIN a kind (two `Alice` folders
 * are one Person), and collapsing across kinds as well made a `Alice` person
 * and an `Alice` set count once between them. The number under "what happens
 * when you press the button" then undercounted exactly the libraries that
 * reuse a name at two levels, which is most of them.
 */
const entityCount = computed(() => {
  let total = 0;
  for (const [, bucket] of grouped.value) total += bucket.size;
  return total;
});

/**
 * The kinds this mapping actually has, named for the sentence beside the count.
 *
 * Read from the same `grouped` buckets the total is summed from, so a facet
 * cannot be counted by the number and left out of the sentence - which is what
 * happened to Tag. Only the non-empty ones: a single mapped project used to
 * read "1 projects, sets, people and tags are created", naming three kinds that
 * were not there and disagreeing with its own verb.
 *
 * Nothing mapped is the exception, and it is not a special case: "0 projects,
 * sets, people and tags" is true of every kind at once, so all four are named.
 */
const entityKinds = computed(() => {
  const present = FACET_KINDS.filter(
    (kind) => grouped.value.get(kind.value)?.size,
  );
  const names = (present.length ? present : FACET_KINDS).map((kind) =>
    (entityCount.value === 1 ? kind.label : kind.plural).toLowerCase(),
  );
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
});

async function poll(taskId) {
  if (disposed) return;
  try {
    const body = await getFolderStructureCommitStatus(taskId);
    if (disposed) return;
    stage.value = body.stage;
    processed.value = body.processed;
    total.value = body.total;
    if (body.status === "failed") {
      committing.value = false;
      commitError.value = body.error || "The import failed.";
      return;
    }
    if (body.status === "abandoned" || body.status === "deferred") {
      // Neither is a failure and neither is a finished mapping, so this
      // reports no result: the pictures indexed before the stop stay, and
      // the wizard closes leaving the saved read for another day.
      committing.value = false;
      emit("cancel");
      return;
    }
    if (body.status === "completed") {
      committing.value = false;
      emit("committed", body.result);
      return;
    }
    pollTimer = setTimeout(() => poll(taskId), 300);
  } catch (error) {
    if (disposed) return;
    committing.value = false;
    commitError.value = errorDetail(error) || "The import failed.";
  }
}

/**
 * `captions` is the second thing a caller decides, beside the assignments: a
 * commit sends the answers the owner actually saw on the card, and `[]` -
 * "answered, read nothing" - whenever there was no card to see. Defaulting it
 * from the card is only right for "Yes, build this library"; the commit that
 * starts on mount, and Organise later, both pass their own.
 */
async function commit(
  assignments = props.assignments,
  captions = chosenCaptions(),
) {
  if (!props.libraryExists) {
    emit("build", assignments, captions);
    return;
  }
  committing.value = true;
  commitError.value = "";
  try {
    const started = await startFolderStructureCommit(
      props.readTaskId,
      assignments,
      props.label,
      props.mode,
      props.readResult,
      captions,
    );
    commitTaskId.value = started.task_id;
    emit("commit-started", started.task_id);
    poll(started.task_id);
  } catch (error) {
    committing.value = false;
    commitError.value = errorDetail(error) || "Could not start the import.";
  }
}

/**
 * "Organise later" - index everything now, decide what the folders mean some
 * other day.
 *
 * Before the import starts this is a commit with NO assignments, which is the
 * whole correction: closing the wizard instead used to leave a library that
 * had been created and never filled, and an empty library is what "Cancel"
 * means, not what "later" means. Once the import is running the same words
 * mean the same thing - keep every picture already indexed, apply none of the
 * mapping - which is what `stop=defer` does server-side.
 *
 * No assignments and no caption answers: this is the owner declining to
 * decide, and the read's guesses are not their answer. After a complete read
 * `[]` says "read nothing" rather than leaving the import to probe; after a
 * partial one nobody could have been asked about the unwalked part, so the
 * answer stays `null` and the import probes, as `chosenCaptions` and the
 * wizard's own `later()` do.
 */
async function organiseLater() {
  if (!committing.value) {
    commit([], patternsPartial.value ? null : []);
    return;
  }
  try {
    await stopFolderStructureCommit(commitTaskId.value, "defer");
  } catch (error) {
    commitError.value = errorDetail(error) || "Could not stop the import.";
  }
}

/** Give up on the import. What was indexed before now stays indexed. */
async function abort() {
  try {
    await stopFolderStructureCommit(commitTaskId.value, "abort");
  } catch (error) {
    commitError.value = errorDetail(error) || "Could not stop the import.";
  }
}

onMounted(() => {
  // The card is never seen on this path (the commit is already running by the
  // first paint), so the saved answers are the only ones the owner gave. The
  // read's guesses are not an answer, and mapping them here committed a
  // convention nobody confirmed. `null` - an entry saved before the card
  // existed - is not an answer either, and goes through as `null` so the
  // request omits the key.
  if (props.commitOnMount) commit(props.assignments, props.captions);
});

onUnmounted(() => {
  disposed = true;
  if (pollTimer) clearTimeout(pollTimer);
});
</script>

<template>
  <div class="preview-step">
    <!-- The heading is the dialog's title ("This is what your folders
         become"), and the way back is the button in the dialog's header beside
         it, where a step's chrome belongs. This step draws no second one. -->
    <div class="preview-step__groups">
      <!-- Only the kinds this mapping has: an empty group would still take a
           cell of the grid and leave a hole beside the one that follows. -->
      <template v-for="kind in FACET_KINDS" :key="kind.value">
        <div
          v-if="grouped.get(kind.value)?.size"
          class="preview-step__group"
          :style="kindStyle(kind.value)"
        >
          <div class="preview-step__group-title">
            <v-icon size="15">{{ kind.icon }}</v-icon>
            {{ grouped.get(kind.value).size }}
            {{ grouped.get(kind.value).size === 1 ? kind.label : kind.plural }}
          </div>
          <div class="preview-step__chips">
            <span
              v-for="name in [...grouped.get(kind.value).keys()].slice(0, 24)"
              :key="name"
              class="preview-step__chip"
            >
              {{ name }}
            </span>
            <span
              v-if="grouped.get(kind.value).size > 24"
              class="preview-step__chip preview-step__chip--muted"
            >
              {{ grouped.get(kind.value).size - 24 }} more
            </span>
          </div>
        </div>
      </template>
    </div>

    <!-- Hidden once the commit is running: a resumed post-switch commit starts
         on mount, and a card of questions whose every select is disabled is a
         decision the owner can no longer make.

         Shown with no rows at all when the read stopped early: an empty list
         under "not every folder was checked" says the list is incomplete,
         which is the one thing silence cannot say. -->
    <div
      v-if="(patterns.length || patternsPartial) && !committing"
      class="preview-step__card preview-step__card--captions"
    >
      <div class="preview-step__card-title">Caption files beside your pictures</div>
      <p class="preview-step__card-lead">
        Text files named after a picture are read once, during this import,
        as its tags or description. Nothing is written back to them. Check
        each pattern.
      </p>
      <p v-if="patternsPartial" class="preview-step__card-lead">
        Not every folder was checked; only the patterns found so far are
        listed.
      </p>
      <ul class="preview-step__captions">
        <li
          v-for="row in patterns"
          :key="row.suffix"
          class="preview-step__caption"
          :class="{
            'preview-step__caption--ignored':
              (answers[row.suffix] ?? row.kind) === 'ignore',
          }"
        >
          <div class="preview-step__caption-what">
            <code class="preview-step__caption-suffix">*{{ row.suffix }}</code>
            <span class="preview-step__caption-count">
              {{ row.files.toLocaleString() }}
              {{ row.files === 1 ? "file" : "files" }} in
              {{ row.folders.toLocaleString() }}
              {{ row.folders === 1 ? "folder" : "folders" }}
            </span>
            <span class="preview-step__caption-sample" :title="row.sample">
              {{ row.sample }}
            </span>
          </div>
          <AppSelect
            :model-value="answers[row.suffix]"
            :options="CAPTION_CHOICES"
            :label="`Read *${row.suffix} as`"
            hide-label
            compact
            :disabled="committing"
            class="preview-step__caption-choice"
            @update:model-value="answerChosen(row.suffix, $event)"
          />
        </li>
      </ul>
    </div>

    <div class="preview-step__card">
      <div class="preview-step__card-title">
        What happens when you press the button
      </div>
      <div class="preview-step__facts">
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark preview-step__fact-mark--yes"
            >✓</span
          >
          {{ pictureCount.toLocaleString() }} picture(s) are indexed where they
          already are
        </div>
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark preview-step__fact-mark--yes"
            >✓</span
          >
          {{ entityCount.toLocaleString() }} {{ entityKinds }}
          {{ entityCount === 1 ? "is" : "are" }} created or matched
        </div>
        <!-- Per kind: only a tags file spares the tagger. A description file
             leaves the picture with no tags of its own, so counting the two
             together claimed a saving the tagger never gets. -->
        <div v-if="captionFilesAsTags" class="preview-step__fact">
          <span class="preview-step__fact-mark preview-step__fact-mark--yes"
            >✓</span
          >
          {{ captionFilesAsTags.toLocaleString() }} caption
          {{ captionFilesAsTags === 1 ? "file is" : "files are" }} read as tags;
          {{ captionFilesAsTags === 1 ? "that picture is" : "those pictures are" }}
          not tagged from scratch
        </div>
        <div v-if="captionFilesAsDescriptions" class="preview-step__fact">
          <span class="preview-step__fact-mark preview-step__fact-mark--yes"
            >✓</span
          >
          {{ captionFilesAsDescriptions.toLocaleString() }} caption
          {{ captionFilesAsDescriptions === 1 ? "file is" : "files are" }} read
          as descriptions
        </div>
        <div v-if="captionFilesIgnored" class="preview-step__fact">
          <span class="preview-step__fact-mark">—</span>
          {{ captionFilesIgnored.toLocaleString() }} caption
          {{ captionFilesIgnored === 1 ? "file is" : "files are" }} left
          unread
        </div>
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark">—</span>
          no file is copied, moved or renamed
        </div>
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark">—</span>
          {{ lastFact }}
        </div>
      </div>
    </div>

    <p v-if="commitError" class="preview-step__error" role="alert">
      {{ commitError }}
    </p>

    <div v-if="committing" class="preview-step__progress">
      <v-progress-circular indeterminate size="18" width="2" color="accent" />
      <span>
        <template v-if="stage === 'indexing'"
          >indexing pictures - {{ processed }} of {{ total }}</template
        >
        <template v-else-if="stage === 'registering'"
          >registering the folder…</template
        >
        <template v-else-if="stage === 'assigning'"
          >creating projects, people, sets and tags…</template
        >
        <template v-else>working…</template>
      </span>
    </div>

    <div class="preview-step__actions">
      <AppButton v-if="!committing" variant="primary" @click="commit()">
        Yes, build this library
      </AppButton>
      <!-- Organise later stays available WHILE the import runs: it is the
           answer to "this is taking ages and I do not want to watch", and it
           means the same thing at both moments - index it all, map it later.
           Both stops are disabled for the seconds between `committing` going
           true and the server answering with a task id: there is nothing to
           stop yet, and pressing them then sent `stop("")`, which fails with
           "Could not stop the import." while the mapping commits anyway.
           Disabled rather than a silent early return, because a live button
           that does nothing is the same bug with the error message removed. -->
      <AppButton
        variant="secondary"
        :disabled="committing && !commitTaskId"
        @click="organiseLater"
      >
        Organise later
      </AppButton>
      <AppButton
        v-if="committing"
        variant="ghost"
        :disabled="!commitTaskId"
        @click="abort"
      >
        Abort
      </AppButton>
      <AppButton v-else variant="ghost" @click="emit('cancel')">
        Cancel
      </AppButton>
    </div>
    <p class="preview-step__actions-note">
      <template v-if="committing">
        Organise later keeps every picture indexed so far and leaves the folder
        mapping for another day. Abort gives up on the import - nothing already
        indexed is removed, and no file is touched either way.
      </template>
      <template v-else>
        Organise later brings the pictures in now and leaves naming the folders
        until later. Cancel brings nothing in at all.
      </template>
    </p>
  </div>
</template>

<style scoped>
/* The step owns the dialog body (the wizard mounts it flush, like the mapping
   step) and fills it: the header, the facts and the buttons keep their size,
   and only the two lists - the entity groups and the caption patterns -
   shrink and scroll inside themselves. The step's own scroll is the fallback
   for a window too short even for that. */
.preview-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  flex: 1;
  min-height: 0;
  min-width: 0;
  padding: var(--space-6);
  overflow-y: auto;
}

.preview-step > * {
  flex-shrink: 0;
}

/* The groups flow: each is its title followed by its chips on one wrapping
   line, sized to its content, so a Project and three Sets share a row and a
   group with many chips takes what it needs and no more. The block shrinks
   into its own scrollbar before the facts and buttons below give way. */
.preview-step__groups {
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  align-items: flex-start;
  gap: var(--space-3) var(--space-6);
  flex: 0 1 auto;
  min-height: 2rem;
  overflow-y: auto;
  padding-right: var(--space-2);
}

.preview-step__card--captions {
  display: flex;
  flex-direction: column;
  flex: 0 1 auto;
  min-height: 6rem;
}

.preview-step__group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  max-width: 100%;
}

.preview-step__group-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-right: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
}

/* The chips are items of the group's own wrap, beside the title. */
.preview-step__chips {
  display: contents;
}

.preview-step__group-title .v-icon {
  color: rgb(var(--kind));
}

.preview-step__chip {
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-pill, 999px);
  background: rgb(var(--v-theme-panel));
  box-shadow: inset 3px 0 0 rgb(var(--kind));
  font-size: var(--text-xs);
}

.preview-step__chip--muted {
  color: rgba(var(--v-theme-on-background), 0.55);
}

.preview-step__card {
  padding: var(--space-5);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.preview-step__card-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  margin-bottom: var(--space-4);
}

.preview-step__card-lead {
  margin: calc(-1 * var(--space-2)) 0 var(--space-4);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.65);
}

.preview-step__captions {
  list-style: none;
  margin: 0;
  padding: 0 var(--space-2) 0 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.preview-step__caption {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
}

.preview-step__caption--ignored .preview-step__caption-what {
  opacity: 0.55;
}

.preview-step__caption-what {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-2) var(--space-3);
  min-width: 0;
  font-size: var(--text-sm);
}

.preview-step__caption-suffix {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-panel));
}

.preview-step__caption-count {
  color: rgba(var(--v-theme-on-background), 0.72);
}

.preview-step__caption-sample {
  flex-basis: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.55);
}

.preview-step__caption-choice {
  flex-shrink: 0;
  width: 10rem;
}

.preview-step__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.preview-step__fact {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.preview-step__fact-mark {
  color: rgba(var(--v-theme-on-background), 0.5);
}

.preview-step__fact-mark--yes {
  color: rgb(var(--v-theme-success));
}

.preview-step__actions-note {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.65);
}

.preview-step__error {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  font-size: var(--text-sm);
}

.preview-step__progress {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.72);
}

.preview-step__actions {
  display: flex;
  gap: var(--space-3);
}
</style>
