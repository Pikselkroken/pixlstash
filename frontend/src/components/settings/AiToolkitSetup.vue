<script setup>
// Settings › AI-toolkit, setup for a local install: find the checkout, check
// that it can train, connect. Docker, another machine and the queue prompts are
// not built here; only the static "Other install types" note is.
//
// Invariant: the two paths are only saved once a check has passed on them, so
// a saved install is one PixlStash has seen train.
import { computed, nextTick, ref, watch } from "vue";
import { VIcon, VProgressCircular, VSwitch } from "vuetify/components";
import { useTrainingStore } from "../../stores/useTrainingStore";
import { errorMessage } from "../../utils/apiError";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";
import SettingsSection from "./SettingsSection.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const store = useTrainingStore();

const SETUP_DESC =
  "Train LoRAs of your people and picture sets. PixlStash hands AI-toolkit a dataset and takes the finished run back onto the model shelf.";

// idle | probing | found | not-found | connected
const phase = ref("idle");
// The checkout and python a check is run on, or that Connect would save.
const candidate = ref(null);
const checking = ref(false);
const busy = ref(false);
const checkError = ref("");
const connectError = ref("");
const statusError = ref("");
const formError = ref("");
const searched = ref([]);
const folderPath = ref("");
const browseOpen = ref(false);
// What the live region says. One line per change of state.
const liveText = ref("");

const autoCheckError = ref("");

const connectButton = ref(null);
const checkAgainButton = ref(null);
const chooseButton = ref(null);

const NUMBER_WORDS = [
  "zero",
  "one",
  "two",
  "three",
  "four",
  "five",
  "six",
  "seven",
  "eight",
  "nine",
];

// ── Derived ──────────────────────────────────────────────────────────────────

const showSpinner = computed(
  () => phase.value === "probing" || checking.value || busy.value,
);

// A check passes when the trainer started and the trial loaded it. A reply
// with `trial_ok: false` is a failure even if `ok` is set, because the
// sentence under it says the trial ran.
const checkPassed = computed(() => {
  const c = store.aiToolkitCheck;
  return !!c?.ok && c.trial_ok !== false;
});

// The python Connect saves is the one the check resolved, so a hand-picked
// folder (python not given) connects once the check says where its python is.
const checkedPython = computed(() => store.aiToolkitCheck?.python || null);
const canConnect = computed(() => checkPassed.value && !!checkedPython.value);
const showNoPython = computed(
  () =>
    (phase.value === "found" || phase.value === "not-found") &&
    checkPassed.value &&
    !checkedPython.value,
);

const gpuSentence = computed(() => {
  const gpus = store.aiToolkitCheck?.gpus || [];
  const n = gpus.length;
  if (n === 0) return "no GPU";
  const sizes = gpus.map((g) => `${Math.round(g.total_mb / 1024)} GB`);
  if (n === 1) return `one GPU with ${sizes[0]}`;
  const count = n < NUMBER_WORDS.length ? NUMBER_WORDS[n] : String(n);
  const same = sizes.every((s) => s === sizes[0]);
  return same
    ? `${count} GPUs with ${sizes[0]} each`
    : `${count} GPUs, ${sizes.join(", ")}`;
});

// "Its venv has torch 2.13 with CUDA and sees one GPU with 24 GB. A trial
// start loaded the trainer and exited without training."
const checkSentence = computed(() => {
  const c = store.aiToolkitCheck;
  if (!checkPassed.value) return "";
  const cuda = c.cuda ? " with CUDA" : "";
  return `Its venv has torch ${c.torch || "unknown"}${cuda} and sees ${gpuSentence.value}. A trial start loaded the trainer and exited without training.`;
});

// Which recipes the checked checkout can run: a recipe is offered only when
// the checkout ships its model code.
const recipesSentence = computed(() => {
  const recipes = store.aiToolkitCheck?.presets || [];
  if (!recipes.length) return "";
  const offered = recipes.filter((r) => r.offered).map((r) => r.label);
  if (offered.length) return `It can train ${offered.join(", ")}.`;
  const needs = [...new Set(recipes.map((r) => r.requires_arch))].join(", ");
  return `It has none of the model code PixlStash's recipes need (${needs}), so it cannot train any of them yet. Update the checkout.`;
});

const discovery = computed(() => store.aiToolkitDiscovery);
const status = computed(() => store.aiToolkitStatus);

// The folders: from the check before Connect, from the saved install's status
// after it (the check's own folders while the status is still loading).
const folders = computed(() =>
  phase.value === "connected"
    ? status.value || store.aiToolkitCheck
    : store.aiToolkitCheck,
);
const showFolders = computed(
  () =>
    !!folders.value &&
    !!(folders.value.datasets || folders.value.runs) &&
    (phase.value === "found" ||
      phase.value === "connected" ||
      (phase.value === "not-found" && checkPassed.value)),
);
const shelfLine = computed(() =>
  folders.value?.runs?.on_shelf === false
    ? "Runs land in your model shelf's ai-toolkit folder. PixlStash adds this folder to the shelf when the first run starts."
    : "Runs land in your model shelf's ai-toolkit folder.",
);

// ── Flow ─────────────────────────────────────────────────────────────────────

async function runCheck() {
  const target = candidate.value;
  if (!target) return;
  checking.value = true;
  checkError.value = "";
  liveText.value = "Checking it can train…";
  try {
    const reply = await store.checkInstall(target.repo_path, target.python);
    if (checkPassed.value) {
      liveText.value = "AI-toolkit can train.";
    } else {
      checkError.value =
        reply?.detail || "AI-toolkit could not start a training run here.";
      liveText.value = "AI-toolkit cannot train here.";
    }
  } catch (e) {
    console.warn("[settings] AI-toolkit check failed", target, e);
    checkError.value = errorMessage(e, "Could not check AI-toolkit.");
    liveText.value = "AI-toolkit cannot train here.";
  } finally {
    checking.value = false;
  }
}

async function refreshStatus() {
  statusError.value = "";
  try {
    await store.fetchInstallStatus();
  } catch (e) {
    console.warn("[settings] could not read the AI-toolkit folders", e);
    statusError.value = errorMessage(e, "Could not read the folders.");
  }
}

async function detect() {
  phase.value = "probing";
  candidate.value = null;
  checkError.value = "";
  connectError.value = "";
  liveText.value = "Looking for AI-toolkit on this computer…";
  try {
    const found = await store.discoverInstall();
    searched.value = found?.searched || [];
    if (found?.found) {
      candidate.value = { repo_path: found.repo_path, python: found.python };
      phase.value = "found";
      liveText.value = `Found AI-toolkit on this computer, version ${found.version}.`;
      await runCheck();
    } else {
      phase.value = "not-found";
      liveText.value = "AI-toolkit not found on this computer.";
    }
  } catch (e) {
    console.warn("[settings] could not look for AI-toolkit", e);
    searched.value = [];
    phase.value = "not-found";
    checkError.value = errorMessage(e, "Could not look for AI-toolkit.");
    liveText.value = "AI-toolkit not found on this computer.";
  }
}

async function enterConnected() {
  phase.value = "connected";
  candidate.value = {
    repo_path: store.aiToolkitRepoPath,
    python: store.aiToolkitPython,
  };
  await Promise.all([runCheck(), refreshStatus()]);
}

async function checkTyped() {
  const typed = folderPath.value.trim();
  formError.value = "";
  connectError.value = "";
  if (!typed) {
    formError.value = "Enter the folder AI-toolkit is in.";
    return;
  }
  // Only a checkout with a python the check can resolve gets as far as Connect.
  candidate.value = { repo_path: typed, python: null };
  await runCheck();
}

// The browser is opened from code, so closing it hands focus back to the
// button that opened it (the project rule for programmatic overlays).
function closeBrowser() {
  browseOpen.value = false;
  nextTick(() => chooseButton.value?.focus());
}

function chooseFolder(path) {
  closeBrowser();
  folderPath.value = path;
  checkTyped();
}

async function connect() {
  if (!canConnect.value) return;
  busy.value = true;
  connectError.value = "";
  try {
    await store.saveAiToolkitPaths(
      candidate.value.repo_path,
      checkedPython.value,
    );
  } catch (e) {
    connectError.value = errorMessage(e, "Could not save AI-toolkit.");
    busy.value = false;
    return;
  }
  busy.value = false;
  await refreshStatus();
  phase.value = "connected";
  liveText.value = "Connected to AI-toolkit.";
  await nextTick();
  checkAgainButton.value?.focus();
}

async function checkAgain() {
  busy.value = true;
  try {
    await runCheck();
    await refreshStatus();
  } finally {
    busy.value = false;
  }
}

async function disconnect() {
  busy.value = true;
  connectError.value = "";
  try {
    await store.disconnectAiToolkit();
  } catch (e) {
    connectError.value = errorMessage(e, "Could not disconnect AI-toolkit.");
    busy.value = false;
    return;
  }
  busy.value = false;
  await detect();
  liveText.value = "Disconnected from AI-toolkit.";
  await nextTick();
  (connectButton.value || chooseButton.value)?.focus();
}

// Check pixlstash.dev for newer recipes once a day (off until turned on).
async function setAutoCheck(on) {
  autoCheckError.value = "";
  try {
    await store.setPresetsAutoCheck(on);
  } catch (err) {
    autoCheckError.value = errorMessage(err, "Could not save that setting.");
  }
}

// ── Lifecycle: read the saved install, or look for one, when the tab opens ──
watch(
  () => props.open,
  async (isOpen) => {
    if (!isOpen) return;
    await store.fetchConfig();
    if (store.aiToolkitConfigured) {
      await enterConnected();
    } else {
      await detect();
    }
  },
  { immediate: true },
);
</script>

<template>
  <SettingsSection title="AI-toolkit" :desc="SETUP_DESC" first>
    <div class="ait-card" :aria-busy="showSpinner ? 'true' : 'false'">
      <!-- Always present, so a line that arrives in it is announced. -->
      <div role="status" class="ait-status">
        <p v-if="liveText" class="ait-pending">
          <v-progress-circular v-if="showSpinner" indeterminate size="16" width="2" />
          <span>{{ liveText }}</span>
        </p>
      </div>

      <!-- Found: the two steps before Connect. -->
      <template v-if="phase === 'found'">
        <span class="ait-pip">
          <v-icon size="16">mdi-circle-outline</v-icon>
          Not connected
        </span>
        <div class="ait-stack">
          <p class="ait-title">Find AI-toolkit</p>
          <p class="ait-small ait-muted" data-testid="ait-found-version">
            On this computer, version {{ discovery?.version }}.
          </p>
          <p class="ait-mono ait-muted" data-testid="ait-found-path">
            {{ candidate?.repo_path }}
          </p>
        </div>
        <div class="ait-stack">
          <p class="ait-title">Check it can train</p>
          <p
            v-if="checkSentence"
            class="ait-small ait-muted"
            data-testid="ait-check-sentence"
          >
            {{ checkSentence }}
          </p>
          <p
            v-if="checkSentence && recipesSentence"
            class="ait-small ait-muted"
            data-testid="ait-recipes-sentence"
          >
            {{ recipesSentence }}
          </p>
        </div>
      </template>

      <!-- Not found: say where it looked and offer a folder. -->
      <template v-if="phase === 'not-found'">
        <div class="ait-stack">
          <p class="ait-title">AI-toolkit not found on this computer</p>
          <p v-if="searched.length" class="ait-small ait-muted">
            Searched
          </p>
          <p
            v-for="place in searched"
            :key="place"
            class="ait-mono ait-muted"
          >
            {{ place }}
          </p>
        </div>
        <div class="ait-field">
          <AppInput
            v-model="folderPath"
            label="AI-toolkit folder"
            placeholder="/home/you/ai-toolkit"
            mono
            :error="!!formError"
            :disabled="busy || checking"
            data-testid="ait-folder-input"
            @enter="checkTyped"
          />
          <p v-if="formError" class="ait-error" role="alert">
            <v-icon size="16" class="ait-error__icon">mdi-alert-rhombus-outline</v-icon>
            <span>{{ formError }}</span>
          </p>
        </div>
      </template>

      <!-- Connected: the Ready to train card's header and install facts. -->
      <template v-if="phase === 'connected'">
        <div class="ait-row">
          <span v-if="checkPassed" class="ait-pip ait-pip--ok">
            <v-icon size="16">mdi-check-circle</v-icon>
            Ready to train
          </span>
          <span v-else-if="checking" class="ait-pip">
            <v-progress-circular indeterminate size="16" width="2" />
            Checking
          </span>
          <span v-else class="ait-pip ait-pip--bad">
            <v-icon size="16">mdi-alert-rhombus-outline</v-icon>
            Not ready to train
          </span>
          <span v-if="store.aiToolkitCheck?.version" class="ait-mono ait-muted">
            AI-toolkit {{ store.aiToolkitCheck.version }}
          </span>
        </div>
        <dl class="ait-facts">
          <dt>Install</dt>
          <dd>On this computer</dd>
          <dt>Trains by</dt>
          <dd>
            starting the trainer itself
            <span v-if="store.aiToolkitCheck?.cuda" class="ait-pip ait-pip--ok">
              <v-icon size="14">mdi-check-circle</v-icon>
              CUDA
            </span>
          </dd>
          <template v-if="recipesSentence">
            <dt>Models</dt>
            <dd data-testid="ait-recipes-sentence">{{ recipesSentence }}</dd>
          </template>
        </dl>
      </template>

      <!-- Folders: from the check before Connect, from the status after it. -->
      <div v-if="showFolders" class="ait-stack" data-testid="ait-folders">
        <p class="ait-title">Folders</p>
        <dl class="ait-facts">
          <template v-if="folders.datasets">
            <dt>Datasets</dt>
            <dd>
              <span class="ait-mono">{{ folders.datasets.path }}</span>
              <span
                :class="folders.datasets.writable ? 'ait-pip ait-pip--ok' : 'ait-pip ait-pip--bad'"
                data-testid="ait-datasets-writable"
              >
                {{ folders.datasets.writable ? "writable" : "not writable" }}
              </span>
            </dd>
          </template>
          <template v-if="folders.runs">
            <dt>Runs</dt>
            <dd>
              <span class="ait-mono">{{ folders.runs.path }}</span>
              <span class="ait-pip ait-pip--ok" data-testid="ait-runs-count">
                {{ folders.runs.run_count }} runs
              </span>
            </dd>
          </template>
        </dl>
        <p v-if="folders.runs" class="ait-small ait-muted" data-testid="ait-shelf-line">
          {{ shelfLine }}
        </p>
      </div>

      <p v-if="checkError" class="ait-error" role="alert" data-testid="ait-check-error">
        <v-icon size="16" class="ait-error__icon">mdi-alert-rhombus-outline</v-icon>
        <span>{{ checkError }}</span>
      </p>
      <p v-if="statusError" class="ait-error" role="alert">
        <v-icon size="16" class="ait-error__icon">mdi-alert-rhombus-outline</v-icon>
        <span>{{ statusError }}</span>
      </p>
      <p v-if="connectError" class="ait-error" role="alert" data-testid="ait-connect-error">
        <v-icon size="16" class="ait-error__icon">mdi-alert-rhombus-outline</v-icon>
        <span>{{ connectError }}</span>
      </p>
      <p v-if="showNoPython" class="ait-small ait-muted" data-testid="ait-no-python">
        PixlStash could not find a Python for this folder, so it cannot connect it.
      </p>

      <div v-if="phase === 'found' || phase === 'not-found'" class="ait-row">
        <AppButton
          v-if="checkPassed"
          ref="connectButton"
          variant="primary"
          :loading="busy"
          :disabled="!canConnect || checking"
          data-testid="ait-connect"
          @click="connect"
        >
          Connect
        </AppButton>
        <AppButton
          v-if="phase === 'not-found'"
          variant="secondary"
          :loading="checking"
          :disabled="busy"
          data-testid="ait-check-folder"
          @click="checkTyped"
        >
          Check this folder
        </AppButton>
        <AppButton
          ref="chooseButton"
          variant="ghost"
          :disabled="busy || checking"
          data-testid="ait-choose-folder"
          @click="browseOpen = true"
        >
          Choose folder…
        </AppButton>
        <AppButton
          v-if="phase === 'not-found'"
          variant="ghost"
          :disabled="busy || checking"
          data-testid="ait-search-again"
          @click="detect"
        >
          Search again
        </AppButton>
      </div>

      <div v-if="phase === 'connected'" class="ait-row">
        <AppButton
          ref="checkAgainButton"
          variant="secondary"
          :loading="busy || checking"
          data-testid="ait-check-again"
          @click="checkAgain"
        >
          Check again
        </AppButton>
        <span class="ait-spacer" />
        <AppButton
          variant="ghost"
          :disabled="busy || checking"
          data-testid="ait-disconnect"
          @click="disconnect"
        >
          Disconnect
        </AppButton>
      </div>

      <div class="ait-stack">
        <v-switch
          :model-value="store.presetsAutoCheck === true"
          color="primary"
          density="compact"
          hide-details
          label="Check pixlstash.dev for recipe updates automatically"
          data-testid="ait-recipe-auto-check"
          @update:model-value="setAutoCheck"
        />
        <p class="ait-small ait-muted">
          Once a day, when you open Train a LoRA. A newer recipe can correct
          settings and memory figures; it never changes which model is trained.
        </p>
        <p v-if="autoCheckError" class="ait-error" role="alert">
          <v-icon size="16" class="ait-error__icon">mdi-alert-rhombus-outline</v-icon>
          <span>{{ autoCheckError }}</span>
        </p>
      </div>

      <!-- How this install trains. The same for every local install. -->
      <div class="ait-how">
        <FieldLabel>How this install trains</FieldLabel>
        <div class="ait-how__item">
          <v-icon size="16" class="ait-how__icon">mdi-play-circle-outline</v-icon>
          <div>
            <p class="ait-title">PixlStash starts the trainer itself</p>
            <p class="ait-small ait-muted">
              AI-toolkit's web UI does not need to be open, and runs do not
              appear in its job list. Progress, samples and the finished LoRA
              show in PixlStash.
            </p>
          </div>
        </div>
        <div class="ait-how__other">
          <FieldLabel>Other install types</FieldLabel>
          <p class="ait-small ait-muted">
            In Docker or on another machine, the same three steps connect
            through AI-toolkit's web UI instead, and runs are queued there.
          </p>
        </div>
      </div>
    </div>

    <FolderBrowser
      :open="browseOpen"
      @select="chooseFolder"
      @close="closeBrowser"
    />
  </SettingsSection>
</template>

<style scoped>
.ait-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

/* An empty live region must not add a gap to the card. */
.ait-status:empty {
  display: none;
}

.ait-pending {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.ait-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.ait-spacer {
  flex: 1 1 auto;
}

.ait-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.ait-stack p,
.ait-field p {
  margin: 0;
}

.ait-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.ait-title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
}

.ait-small {
  margin: 0;
  font-size: var(--text-sm);
}

.ait-muted {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.ait-mono {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}

.ait-pip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.ait-pip--ok {
  color: rgb(var(--v-theme-surface-success));
}

.ait-pip--bad {
  color: rgb(var(--v-theme-surface-error));
}

.ait-error {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-error));
}

.ait-error__icon {
  flex-shrink: 0;
  margin-top: var(--space-1);
}

/* Facts: a label column and a value column, the folder rows' shape. */
.ait-facts {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: var(--space-3) var(--space-5);
  margin: 0;
  font-size: var(--text-sm);
}

.ait-facts dt {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  padding-top: var(--space-1);
}

.ait-facts dd {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
}

/* The explainer card: the panel ground, not a second bordered card. */
.ait-how {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-panel));
}

.ait-how__item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
}

.ait-how__icon {
  margin-top: var(--space-1);
  color: rgb(var(--v-theme-surface-success));
}

.ait-how__other {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.ait-how__other p {
  margin: 0;
}
</style>
