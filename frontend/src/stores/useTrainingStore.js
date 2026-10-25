import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";
import {
  cancelTrainingJob,
  createTrainingJob,
  deleteTrainingJob,
  getWorkerProgress,
  listTrainingJobs,
  listTrainingTemplates,
} from "../api/training";
import { getUserConfig, patchUserConfig } from "../api/config";
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

  // ── Panel: config form (seeded from defaults / the chosen template) ─────────
  const templates = ref([]);
  const templatesLoading = ref(false);
  const selectedTemplate = ref(null); // template `name` (with .json)
  const baseModelOverride = ref(null); // null → use template's base model
  const triggerWord = ref("");
  const steps = ref(3000);
  // Advanced settings. Left blank by default so an untouched panel sends NO
  // override for them and the chosen template's golden values apply (the design
  // rule: the only field most users touch is the trigger word). A field is only
  // included in config_overrides when the user actually fills it in.
  const networkRank = ref(null);
  const networkAlpha = ref(null);
  const learningRate = ref("");
  const batchSize = ref(null);
  const saveEvery = ref(null);
  const samplePrompts = ref(""); // newline-separated
  const rawConfig = ref(""); // JSON passthrough → config_overrides

  const submitting = ref(false);
  const submitError = ref("");

  // ── Available VRAM (read once when the panel opens; soft hint only) ─────────
  const vramAvailableGb = ref(null);
  const vramTotalGb = ref(null);

  // ── Jobs (settings section) ──────────────────────────────────────
  const jobs = ref([]);
  const jobsLoading = ref(false);
  const jobsError = ref("");

  // ── Setup config (ai-toolkit paths) ─────────────────────────────────────────
  const aiToolkitRepoPath = ref("");
  const aiToolkitPython = ref("");
  const configLoaded = ref(false);

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
    submitError.value = "";
  }

  /** Seed the panel for a fresh run on the given scope, then open the menu. */
  function openForScope(opts) {
    setScope(opts);
    trainMenuOpen.value = true;
  }

  async function fetchTemplates() {
    templatesLoading.value = true;
    try {
      const data = await listTrainingTemplates();
      templates.value = Array.isArray(data) ? data : [];
      // Default to Z-Image-Turbo if present, else the first template.
      if (!selectedTemplate.value && templates.value.length) {
        const zimage = templates.value.find((t) =>
          (t.name || "").toLowerCase().includes("zimage-turbo"),
        );
        selectedTemplate.value = (zimage || templates.value[0]).name;
      }
    } catch (err) {
      // Non-fatal for the panel; submit still works against the server default.
      console.warn("Failed to load training templates:", err);
    } finally {
      templatesLoading.value = false;
    }
  }

  async function fetchVram() {
    try {
      const data = await getWorkerProgress();
      const usage = data?.process || data?.system || {};
      const total = Number(usage.vram_total_gb);
      const used = Number(usage.vram_used_gb);
      vramTotalGb.value = Number.isFinite(total) ? total : null;
      vramAvailableGb.value =
        Number.isFinite(total) && Number.isFinite(used)
          ? Math.max(0, total - used)
          : null;
    } catch (err) {
      // Soft hint only; leave VRAM unknown on failure.
      console.warn("Failed to read VRAM usage for training hint:", err);
      vramAvailableGb.value = null;
      vramTotalGb.value = null;
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
      // CreateJobRequest forbids extra fields: only set_id/character_id, name,
      // base_model, trigger_word, template, config_overrides are accepted.
      // `steps` (and the advanced fields) go inside config_overrides, never at
      // the top level, or the API rejects the body with a 422.
      const body = {
        ...scopePayload.value,
        name: `${scopeName.value || "LoRA"} (${triggerWord.value})`.trim(),
        trigger_word: triggerWord.value.trim(),
      };
      if (selectedTemplate.value) body.template = selectedTemplate.value;
      if (baseModelOverride.value)
        body.base_model = baseModelOverride.value;

      const overrides = buildConfigOverrides();
      if (overrides && Object.keys(overrides).length)
        body.config_overrides = overrides;

      return await createTrainingJob(body);
    } catch (err) {
      submitError.value =
        err?.response?.data?.detail ||
        err?.message ||
        "Failed to queue training job.";
      throw err;
    } finally {
      submitting.value = false;
    }
  }

  /**
   * Build the config_overrides patch from advanced fields + raw JSON. Raw JSON
   * (when valid) takes precedence and is merged last so a power user can
   * override anything. Throws a friendly error if the raw JSON is malformed.
   */
  function buildConfigOverrides() {
    // A field is only emitted when the user actually provided it, so untouched
    // advanced fields fall through to the template's golden defaults. `steps` is
    // a primary field and is always sent.
    const overrides = {};
    const process = {};
    const network = {};
    if (isFilledNumber(networkRank.value))
      network.linear = Number(networkRank.value);
    if (isFilledNumber(networkAlpha.value))
      network.linear_alpha = Number(networkAlpha.value);
    if (Object.keys(network).length) process.network = network;

    const train = {};
    const lr = String(learningRate.value ?? "").trim();
    if (lr) train.lr = lr;
    if (isFilledNumber(batchSize.value))
      train.batch_size = Number(batchSize.value);
    if (Number.isFinite(Number(steps.value)))
      train.steps = Number(steps.value);
    if (Object.keys(train).length) process.train = train;

    const save = {};
    if (isFilledNumber(saveEvery.value))
      save.save_every = Number(saveEvery.value);
    if (Object.keys(save).length) process.save = save;

    const prompts = samplePrompts.value
      .split("\n")
      .map((p) => p.trim())
      .filter(Boolean);
    // ai-toolkit expects sample.samples as a list of { prompt } objects (the
    // {{TRIGGER_WORD}} placeholder is substituted server-side). When the user
    // leaves this blank we send nothing and the template's default prompts run.
    if (prompts.length)
      process.sample = { samples: prompts.map((prompt) => ({ prompt })) };

    if (Object.keys(process).length)
      overrides.config = { process: [process] };

    const raw = rawConfig.value.trim();
    if (raw) {
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch {
        throw new Error("Raw config is not valid JSON.");
      }
      return deepMerge(overrides, parsed);
    }
    return overrides;
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
   * Optimistically PATCH one ai-toolkit config field; revert on failure (the
   * config-sync contract, integration_architecture.md §12).
   */
  async function patchConfigField(field, value) {
    const refMap = {
      ai_toolkit_repo_path: aiToolkitRepoPath,
      ai_toolkit_python: aiToolkitPython,
    };
    const target = refMap[field];
    if (!target) throw new Error(`Unknown config field: ${field}`);
    const previous = target.value;
    target.value = value; // optimistic
    try {
      await patchUserConfig({ [field]: value });
    } catch (err) {
      target.value = previous; // revert
      throw err;
    }
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
    templates.value = [];
    aiToolkitRepoPath.value = "";
    aiToolkitPython.value = "";
    configLoaded.value = false;
    vramAvailableGb.value = null;
    vramTotalGb.value = null;
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
    templates,
    templatesLoading,
    selectedTemplate,
    baseModelOverride,
    triggerWord,
    steps,
    networkRank,
    networkAlpha,
    learningRate,
    batchSize,
    saveEvery,
    samplePrompts,
    rawConfig,
    submitting,
    submitError,
    vramAvailableGb,
    vramTotalGb,
    fetchTemplates,
    fetchVram,
    startTraining,
    buildConfigOverrides,
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
    patchConfigField,
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

/** Recursive deep-merge of plain objects (arrays/scalars in `b` replace `a`). */
function deepMerge(a, b) {
  if (
    typeof a !== "object" ||
    a === null ||
    Array.isArray(a) ||
    typeof b !== "object" ||
    b === null ||
    Array.isArray(b)
  ) {
    return b;
  }
  const out = { ...a };
  for (const key of Object.keys(b)) {
    out[key] = key in a ? deepMerge(a[key], b[key]) : b[key];
  }
  return out;
}
