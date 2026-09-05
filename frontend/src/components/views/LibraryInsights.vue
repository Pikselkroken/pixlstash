<template>
  <div
    class="ins"
    role="region"
    aria-label="About your library"
    data-testid="library-insights"
  >
    <!-- The shell's top band, same 36px recipe as the grid, Duplicates and the
         model shelf (Toolbar.test.js pins the four together). -->
    <div class="ins-toolbar">
      <!-- An `h2`, not a span: the findings below are `h3`, and without a
           heading above them the screen has no outline for a screen reader to
           move through. The CSS is unchanged - the other view bars carry their
           title in a span and Toolbar.test.js pins this one's size and ink to
           theirs - so this is the outline only, not a visual change. -->
      <h2 class="ins-title">About your library</h2>
      <span class="ins-sub">read-only · nothing here has been changed</span>
      <div class="ins-tb-right">
        <AppButton
          variant="ghost"
          size="sm"
          icon-left="refresh"
          :loading="loading"
          @click="load"
        >
          Look again
        </AppButton>
      </div>
    </div>

    <div class="ins-scroll">
      <p v-if="error" class="ins-state ins-state--error" role="alert">
        {{ error }}
      </p>

      <p v-else-if="loading && !payload" class="ins-state">
        Reading your library…
      </p>

      <template v-else-if="payload">
        <!-- The lede is the whole screen in one sentence, and it leads with
             what was looked AT rather than with what is wrong. -->
        <p class="ins-lede">{{ lede }}</p>
        <!-- What was actually read. A library that is mostly vault-managed
             gets folder findings that say so, and this line is where the
             reader sees why before they read them. Hidden when every picture
             is in a folder, which is the case this release is built for and
             where the sentence would say nothing. -->
        <p v-if="readScope" class="ins-scope">{{ readScope }}</p>

        <div class="ins-list">
          <article
            v-for="finding in payload.findings"
            :key="finding.id"
            :class="[
              'ins-find',
              { 'ins-find--clear': finding.state === 'clear' },
            ]"
            :data-finding="finding.id"
            :data-state="finding.state"
          >
            <span class="ins-mark" aria-hidden="true">
              <v-icon size="22">{{ glyphFor(finding) }}</v-icon>
            </span>
            <div class="ins-body">
              <h3 class="ins-find-title">{{ finding.title }}</h3>
              <p class="ins-evidence">{{ finding.evidence }}</p>
            </div>
            <div class="ins-act">
              <AppButton
                v-if="finding.action"
                variant="primary"
                size="sm"
                @click="emit('act', finding.action)"
              >
                {{ finding.action.label }}
              </AppButton>
              <span v-else class="ins-nothing">nothing to do</span>
              <span v-if="finding.action?.note" class="ins-note-sm">{{
                finding.action.note
              }}</span>
            </div>
          </article>
        </div>

        <!-- The promise the screen is built on, stated where the eye lands
             last. Every button above opens a tool; every one of those tools
             asks before it changes anything. -->
        <p class="ins-footnote">
          This screen only ever reads. Every button on it opens the tool with
          the right pictures already chosen, and every one of those tools asks
          before it changes anything.
        </p>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { VIcon } from "vuetify/components";

import AppButton from "../widgets/AppButton.vue";
import { getInsights } from "../../api/insights";

const emit = defineEmits(["act"]);

// One glyph per check. Keyed on the finding id rather than on its prose, so a
// reworded finding does not silently lose its icon.
const GLYPHS = {
  unsorted_pile: "mdi-folder-question-outline",
  overlapping_folders: "mdi-folder-multiple-outline",
  uncaptioned: "mdi-text-box-search-outline",
  unnamed_faces: "mdi-account-question-outline",
  untagged: "mdi-tag-off-outline",
};

const loading = ref(false);
const error = ref("");
const payload = ref(null);

/** A check that came back clear wears a tick, whatever it was checking. */
function glyphFor(finding) {
  if (finding.state === "clear") return "mdi-check-circle-outline";
  return GLYPHS[finding.id] || "mdi-information-outline";
}

const lede = computed(() => {
  const data = payload.value;
  if (!data) return "";
  const total = Number(data.total_pictures || 0).toLocaleString();
  const todo = data.findings.filter((f) => f.state === "todo").length;
  const checks = data.findings.length;
  if (!data.total_pictures) {
    return (
      "There are no pictures here yet. Add the folder you already organised " +
      "and PixlStash will read it in place - nothing moves, nothing is renamed."
    );
  }
  const looked = `${checks} things worth knowing about the ${total} pictures you already had.`;
  if (todo === 0) {
    return `${looked} PixlStash looked at all ${checks} and has nothing to suggest.`;
  }
  const clear = checks - todo;
  // The all-todo case is the messy library this release exists for, and the
  // first version of this sentence told it "the 0 below them are fine as they
  // are". Three branches, because the tail clause is only true when there IS
  // a tail.
  if (clear === 0) {
    return `${looked} PixlStash has something to suggest about every one of them.`;
  }
  return (
    `${looked} ${todo} of them ${todo === 1 ? "is" : "are"} worth a look; ` +
    `the ${clear === 1 ? "one" : clear} below ${
      clear === 1 ? "it is fine as it is" : "them are fine as they are"
    }.`
  );
});

// Null when there is nothing worth saying: an empty library, or one whose
// every picture sits in a folder PixlStash reads in place.
const readScope = computed(() => {
  const data = payload.value;
  if (!data || !data.total_pictures) return "";
  const inFolders = Number(data.folder_pictures || 0);
  if (inFolders >= data.total_pictures) return "";
  if (!inFolders) {
    return "None of them are in folders PixlStash reads in place, so there are no folder names of yours to report on.";
  }
  return (
    `${inFolders.toLocaleString()} of them sit in ${Number(data.folders).toLocaleString()} folders ` +
    "PixlStash reads in place; the rest live in the vault under names it chose, and have no folder name of yours."
  );
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    payload.value = await getInsights();
  } catch (err) {
    // `detail` is a STRING on a raised HTTPException and a list of objects on
    // a FastAPI validation error, so it cannot go straight into the template:
    // the second shape renders as "[object Object]", which tells the owner
    // nothing and looks like a crash.
    const detail = err?.response?.data?.detail;
    error.value =
      (typeof detail === "string" && detail) ||
      err?.message ||
      "Could not read the library just now.";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.ins {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
}

/* The shell's top band. Copied declaration-for-declaration from
   `.selection-bar-overlay` (Toolbar.vue), which is the point of truth for the
   36px recipe: `height` + `box-sizing: border-box` + zero vertical padding,
   never `min-height` + vertical padding. Toolbar.test.js pins this bar to the
   other three, so a fourth view cannot step the content area on a switch. */
.ins-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 36px;
  box-sizing: border-box;
  /* Right inset matches the grid bar's (the app-wide tail must land at the
     same distance from the edge in every view); left is this view's own
     content gutter, shared with `.ins-scroll`. */
  padding: 0 var(--space-3) 0 var(--space-5);
  background: rgb(var(--v-theme-toolbar));
  color: rgb(var(--v-theme-toolbar-text));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

/* Same size and ink as `.qtitle` / `.qsub` on the Duplicates bar, so the two
   destinations read as one bar in two contexts. */
.ins-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  min-width: 0;
  flex-shrink: 6;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ins-sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-toolbar-text), 0.6);
  white-space: nowrap;
}

.ins-tb-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.ins-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* --space-7 down the sides rather than the list gutter used elsewhere: this
     is a reading surface, not a table, and the measure below is capped so the
     evidence lines stay readable on a wide window. */
  padding: var(--space-6) var(--space-7) var(--space-8);
}

.ins-state {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.6);
}

.ins-state--error {
  color: rgb(var(--v-theme-error));
}

.ins-lede {
  margin: 0 0 var(--space-6);
  max-width: 72ch;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-background), 0.7);
}

.ins-scope {
  margin: calc(-1 * var(--space-4)) 0 var(--space-6);
  max-width: 72ch;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.5);
  line-height: var(--leading-body);
}

.ins-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  /* The CARD is wide; the prose inside it is not. Capping the card on a
     character measure (the first shape this had) squeezed the action column
     until its note wrapped mid-phrase, on a window with 500px of unused space
     beside it. The measure belongs on `.ins-evidence`, which has one. This cap
     is here only so the row does not stretch to 2000px on a wide monitor. */
  max-width: 1120px;
}

.ins-find {
  display: flex;
  align-items: flex-start;
  gap: var(--space-5);
  padding: var(--space-5);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
  background: rgb(var(--v-theme-surface));
}

/* A check that came back clear is a quieter card, not a missing one. Dashed
   and unpainted: present, deliberately not competing with the rows that have
   something in them. */
.ins-find--clear {
  background: transparent;
  border-style: dashed;
}

.ins-mark {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: var(--hover-wash);
  color: rgb(var(--v-theme-accent));
}

.ins-find--clear .ins-mark {
  background: transparent;
  color: rgba(var(--v-theme-on-background), 0.45);
}

.ins-body {
  flex: 1;
  min-width: 0;
}

.ins-find-title {
  margin: 0 0 var(--space-2);
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
}

.ins-evidence {
  margin: 0;
  max-width: 72ch;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-background), 0.65);
}

.ins-act {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--space-2);
  text-align: right;
}

/* The note under a button says what it opens, not what it will do to your
   pictures - nothing on this screen does anything to your pictures. */
.ins-note-sm {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-background), 0.5);
  max-width: 26ch;
}

.ins-nothing {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-background), 0.45);
  padding: var(--space-1) 0;
}

.ins-footnote {
  margin: var(--space-6) 0 0;
  max-width: 72ch;
  padding: var(--space-3) var(--space-5);
  border-left: 3px solid rgb(var(--v-theme-accent));
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  background: var(--hover-wash);
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* Narrow: the action drops under the evidence rather than squeezing the
   measure, and the bar loses its subtitle the way the other view bars do. */
@media (max-width: 900px) {
  .ins-find {
    flex-wrap: wrap;
  }
  .ins-act {
    width: 100%;
    flex-direction: row;
    align-items: center;
    justify-content: flex-start;
    text-align: left;
  }
  .ins-sub {
    display: none;
  }
  .ins-scroll {
    padding: var(--space-5) var(--space-5) var(--space-8);
  }
}
</style>
