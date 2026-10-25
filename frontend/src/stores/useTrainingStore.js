import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";
import {
  cancelTrainingJob,
  checkAiToolkit,
  createTrainingJob,
  deleteTrainingJob,
  discoverAiToolkit,
  getAiToolkitStatus,
  listTrainingJobs,
  listTrainingPresets,
  putAiToolkitConfig,
} from "../api/training";
import { getUserConfig } from "../api/config";
import { DEFAULT_TIMEOUTS, useNoticeStore } from "./useNoticeStore";
import { onSessionReset } from "../utils/apiClient";
import { useSidebarStore } from "./useSidebarStore";

/**
 * Cross-component state for the LoRA training feature: the train-config panel
 * (TbTrainPanel), the training registry settings section (TrainingSection), and
 * the entry points in the Toolbar / SideBar.
 *
 * Mirrors useExportStore (panel-bound config) + useSnapshotsStore (resource
 * data + actions). Every HTTP call goes through api/training.js or api/config.js;
 * the registry actions surface the API `detail` to the caller for inline error
 * display. All training endpoints are owner-only on the backend.
 */
export const useTrainingStore = defineStore("training", () => {
  const noticeStore = useNoticeStore();
  const sidebarStore = useSidebarStore();

  // ── Panel: scope (set XOR character the run trains on) ──────────────────────
  // Set by the Toolbar/SideBar entry points before the panel opens. Exactly one
  // of setId / characterId is non-null; the API enforces the XOR.
  const trainMenuOpen = ref(false);
  const scopeType = ref(null); // "set" | "character" | null
  const scopeId = ref(null); // numeric set_id or character_id
  const scopeName = ref(""); // display name for the panel header
  const scopePictureCount = ref(0); // pictures currently in view (header hint)

  // ── Panel: config form (the recipe and the three choices it resolves) ──────
  const presets = ref([]); // GET /training/presets: { id, label, offered, … }
  const presetsLoading = ref(false);
  const selectedPreset = ref(null); // recipe id
  const triggerWord = ref("");
  // What the LoRA learns; a character is always a person, a set must choose.
  const subject = ref(null); // "person" | "style" | "thing"
  const effort = ref("standard"); // "gentle" | "standard" | "strong"
  const fit = ref("auto"); // "auto" | "save_memory" | "best_quality"
  // Advanced settings. Left blank by default so an untouched panel sends NO
  // pin and the recipe's derived values apply. A field is only sent as a pin
  // when the user actually fills it in.
  const steps = ref(null);
  const networkRank = ref(null);
  const networkAlpha = ref(null);
  const learningRate = ref("");
  const batchSize = ref(null);
  const saveEvery = ref(null);
  const samplePrompts = ref(""); // newline-separated

  const submitting = ref(false);
  const submitError = ref("");

  // ── Jobs (settings section) ──────────────────────────────────────
  const jobs = ref([]);
  const jobsLoading = ref(false);
  const jobsError = ref("");

  // ── Setup config (ai-toolkit paths) ─────────────────────────────────────────
  const aiToolkitRepoPath = ref("");
  const aiToolkitPython = ref("");
  const configLoaded = ref(false);

  // ── ai-toolkit setup flow: the last discover, check and status replies ─────
  const aiToolkitDiscovery = ref(null); // { found, repo_path, python, version, searched }
  const aiToolkitCheck = ref(null); // { ok, version, torch, cuda, gpus, trial_ok, detail }
  const aiToolkitStatus = ref(null); // { configured, repo_path, python, version, datasets, runs }

  // ── Deep-link request: open Settings on the Training entry ─────────────────
  const settingsTabRequest = ref(0); // bump to request; App.vue watches

  // ── Derived ─────────────────────────────────────────────────────────────────
  const aiToolkitConfigured = computed(
    () => !!(aiToolkitRepoPath.value && aiToolkitPython.value),
  );

  const TERMINAL_STATUSES = ["completed", "error", "stopped"];
  const isTerminalStatus = (status) => TERMINAL_STATUSES.includes(status);

  const hasRunningJob = computed(() =>
    jobs.value.some((j) => !isTerminalStatus(j.status)),
  );

  /** The set_id / character_id payload for POST /training/jobs (XOR). */
  const scopePayload = computed(() => {
    if (scopeType.value === "set") return { set_id: scopeId.value };
    if (scopeType.value === "character")
      return { character_id: scopeId.value };
    return {};
  });

  // ── Panel actions ────────────────────────────────────────────────────────────

  /**
   * Seed the panel's scope (set XOR character) without touching the open flag:
   * the Toolbar's v-menu owns open/close. Defaults the trigger word to the
   * scope name. Picture count is optional and may be filled in later by the
   * App-level count refresh.
   */
  function setScope({ type, id, name, pictureCount }) {
    scopeType.value = type;
    scopeId.value = id != null ? Number(id) : null;
    scopeName.value = name || "";
    if (pictureCount != null) scopePictureCount.value = Number(pictureCount) || 0;
    // Default the trigger word to the scope name (normalised lazily by backend).
    triggerWord.value = (name || "").trim();
    subject.value = type === "character" ? "person" : null;
    submitError.value = "";
  }

  /** Seed the panel for a fresh run on the given scope, then open the menu. */
  function openForScope(opts) {
    setScope(opts);
    trainMenuOpen.value = true;
  }

  async function fetchPresets() {
    presetsLoading.value = true;
    try {
      const data = await listTrainingPresets();
      presets.value = Array.isArray(data) ? data : [];
      // Default to the first recipe the checkout can run, else the first.
      if (!selectedPreset.value && presets.value.length) {
        const usable = presets.value.find((p) => p.offered);
        selectedPreset.value = (usable || presets.value[0]).id;
      }
    } catch (err) {
      // Non-fatal for the panel; submit still works against the server default.
      console.warn("Failed to load training recipes:", err);
    } finally {
      presetsLoading.value = false;
    }
  }

  /**
   * Submit POST /training/jobs from the current panel state. Returns the created
   * job on success; throws (with API detail in submitError) on failure.
   */
  async function startTraining() {
    submitting.value = true;
    submitError.value = "";
    try {
      // CreateJobRequest forbids extra fields. The base model is the
      // recipe's, read by the server, and the advanced fields go in `pins`.
      const body = {
        ...scopePayload.value,
        name: `${scopeName.value || "LoRA"} (${triggerWord.value})`.trim(),
        trigger_word: triggerWord.value.trim(),
        effort: effort.value,
        fit: fit.value,
      };
      if (selectedPreset.value) body.preset = selectedPreset.value;
      if (subject.value) body.subject = subject.value;

      const pins = buildPins();
      if (Object.keys(pins).length) body.pins = pins;

      return await createTrainingJob(body);
    } catch (err) {
      // A run that fits nowhere answers { message, budget, need_mb, free_mb }.
      const detail = err?.response?.data?.detail;
      submitError.value =
        detail?.message ||
        (typeof detail === "string" ? detail : "") ||
        err?.message ||
        "Failed to queue training job.";
      throw err;
    } finally {
      submitting.value = false;
    }
  }

  /**
   * The owner's Advanced values as pins, keyed by the server's tunable names.
   * Only filled-in fields are sent; the rest come from the recipe.
   */
  function buildPins() {
    const pins = {};
    if (isFilledNumber(steps.value)) pins["train.steps"] = Number(steps.value);
    if (isFilledNumber(networkRank.value))
      pins["network.linear"] = Number(networkRank.value);
    if (isFilledNumber(networkAlpha.value))
      pins["network.linear_alpha"] = Number(networkAlpha.value);
    // A typed value that is not a number is sent as typed, so the server
    // refuses it with its own message instead of the field being dropped.
    const lr = String(learningRate.value ?? "").trim();
    if (lr) pins["train.lr"] = Number.isFinite(Number(lr)) ? Number(lr) : lr;
    if (isFilledNumber(batchSize.value))
      pins["train.batch_size"] = Number(batchSize.value);
    if (isFilledNumber(saveEvery.value))
      pins["save.save_every"] = Number(saveEvery.value);
    const prompts = samplePrompts.value
      .split("\n")
      .map((p) => p.trim())
      .filter(Boolean);
    // {{TRIGGER_WORD}} in a prompt is filled in by the server.
    if (prompts.length) pins["sample.samples[*].prompt"] = prompts;
    return pins;
  }

  // ── Registry / jobs actions ───────────────────────────────────────────────────

  async function fetchJobs() {
    jobsLoading.value = true;
    jobsError.value = "";
    try {
      const data = await listTrainingJobs();
      jobs.value = Array.isArray(data) ? data : [];
    } catch (err) {
      jobsError.value =
        err?.response?.data?.detail ||
        err?.message ||
        "Failed to load training jobs.";
    } finally {
      jobsLoading.value = false;
    }
  }

  async function cancelJob(jobId) {
    await cancelTrainingJob(jobId);
    await fetchJobs();
  }

  async function deleteJob(jobId) {
    await deleteTrainingJob(jobId);
    jobs.value = jobs.value.filter((j) => j.id !== jobId);
  }

  // ── Setup config (ai-toolkit paths) ───────────────────────────────────────────

  async function fetchConfig() {
    try {
      const config = await getUserConfig();
      aiToolkitRepoPath.value = config?.ai_toolkit_repo_path || "";
      aiToolkitPython.value = config?.ai_toolkit_python || "";
      configLoaded.value = true;
    } catch (err) {
      console.warn("Failed to load ai-toolkit config:", err);
    }
  }

  /**
   * Save the checkout and its python together, once the check has passed. A
   * refusal (403 for a remote owner without host ops, 422) throws, and the
   * caller shows the server's `detail`.
   */
  async function saveAiToolkitPaths(repoPath, python) {
    await putAiToolkitConfig({ repo_path: repoPath, python });
    aiToolkitRepoPath.value = repoPath;
    aiToolkitPython.value = python;
  }

  /** Forget both paths; the install's check and status go with them. */
  async function disconnectAiToolkit() {
    await putAiToolkitConfig({ repo_path: null, python: null });
    aiToolkitRepoPath.value = "";
    aiToolkitPython.value = "";
    aiToolkitCheck.value = null;
    aiToolkitStatus.value = null;
  }

  async function discoverInstall() {
    aiToolkitDiscovery.value = await discoverAiToolkit();
    return aiToolkitDiscovery.value;
  }

  /** Throws on a 422 (not a checkout); a failed trial comes back as `ok: false`. */
  async function checkInstall(repoPath, python) {
    aiToolkitCheck.value = null;
    aiToolkitCheck.value = await checkAiToolkit({ repo_path: repoPath, python });
    return aiToolkitCheck.value;
  }

  async function fetchInstallStatus() {
    aiToolkitStatus.value = await getAiToolkitStatus();
    return aiToolkitStatus.value;
  }

  // ── Cross-component coordination ──────────────────────────────────────────────

  // A confirmation notice, not a banner: it expires on its own, and its action
  // opens the Tasks tab where the run's progress is.
  function showQueuedToast(message) {
    noticeStore.push({
      level: "success",
      text: message,
      action: { label: "View Tasks", handler: () => sidebarStore.showTasksTab() },
      timeout: DEFAULT_TIMEOUTS.success,
      key: "lora-training-queued",
    });
  }

  function requestTrainingSettings() {
    settingsTabRequest.value += 1;
  }

  // Every route here is owner-only, and the jobs and ai-toolkit paths
  // belong to the credential that read them: a change of credential empties them.
  function resetForSession() {
    jobs.value = [];
    jobsError.value = "";
    presets.value = [];
    aiToolkitRepoPath.value = "";
    aiToolkitPython.value = "";
    configLoaded.value = false;
    aiToolkitDiscovery.value = null;
    aiToolkitCheck.value = null;
    aiToolkitStatus.value = null;
    trainMenuOpen.value = false;
  }

  const unsubscribeSessionReset = onSessionReset(resetForSession);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    // panel scope
    trainMenuOpen,
    scopeType,
    scopeId,
    scopeName,
    scopePictureCount,
    scopePayload,
    setScope,
    openForScope,
    // panel config
    presets,
    presetsLoading,
    selectedPreset,
    triggerWord,
    subject,
    effort,
    fit,
    steps,
    networkRank,
    networkAlpha,
    learningRate,
    batchSize,
    saveEvery,
    samplePrompts,
    submitting,
    submitError,
    fetchPresets,
    startTraining,
    buildPins,
    // jobs
    jobs,
    jobsLoading,
    jobsError,
    hasRunningJob,
    isTerminalStatus,
    fetchJobs,
    cancelJob,
    deleteJob,
    // setup config
    aiToolkitRepoPath,
    aiToolkitPython,
    aiToolkitConfigured,
    configLoaded,
    fetchConfig,
    aiToolkitDiscovery,
    aiToolkitCheck,
    aiToolkitStatus,
    discoverInstall,
    checkInstall,
    fetchInstallStatus,
    saveAiToolkitPaths,
    disconnectAiToolkit,
    // coordination
    settingsTabRequest,
    showQueuedToast,
    requestTrainingSettings,
  };
});

/** True when a value is a non-blank, finite number (an empty field is skipped). */
function isFilledNumber(value) {
  if (value === null || value === undefined || value === "") return false;
  return Number.isFinite(Number(value));
}
