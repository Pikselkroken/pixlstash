import { computed, onScopeDispose, ref, watch } from "vue";
import { defineStore } from "pinia";
import {
  cancelTrainingJob,
  checkAiToolkit,
  checkTrainingPresets,
  createTrainingJob,
  deleteTrainingJob,
  discoverAiToolkit,
  getAiToolkitStatus,
  getTrainingDataset,
  getTrainingSamplePrompts,
  listTrainingJobs,
  listTrainingPresets,
  putAiToolkitConfig,
  resolveTraining,
} from "../api/training";
import { getUserConfig, patchUserConfig } from "../api/config";
import { DEFAULT_TIMEOUTS, useNoticeStore } from "./useNoticeStore";
import { onSessionReset } from "../utils/apiClient";
import { errorMessage } from "../utils/apiError";
import { useSidebarStore } from "./useSidebarStore";
import {
  AUDIO_KEY,
  PROMPTS_KEY,
  displayPrompt,
  promptFromDisplay,
  promptLines,
  promptTexts,
  promptsEdited,
  runName,
  sameValue,
  toggleTrigger,
  triggerFromName,
} from "../utils/trainingPlan";
import { isBusyJob } from "../utils/trainingRuns.js";

// How long the dialog waits after a change before it asks the server again.
const RESOLVE_DEBOUNCE_MS = 150;
const PROMPTS_DEBOUNCE_MS = 400;

/**
 * Cross-component state for the LoRA training feature: the Train a LoRA dialog
 * (TrainLoraDialog), the AI-toolkit settings pane (AiToolkitSetup),
 * and the entry points in the Toolbar / SideBar.
 *
 * The dialog holds the owner's choices and typed values; the server resolves
 * them (POST /training/resolve) into every value the run will use, and the
 * same choices and pins are what Start sends. Every HTTP call goes through
 * api/training.js or api/config.js; the registry actions surface the API
 * `detail` to the caller for inline error display. All training endpoints are
 * owner-only on the backend.
 */
export const useTrainingStore = defineStore("training", () => {
  const noticeStore = useNoticeStore();
  const sidebarStore = useSidebarStore();

  // ── Dialog: what it trains on ──────────────────────────────────────────────
  // The entry point (Toolbar / SideBar) names a set or a character. The
  // dataset read says whether a set is a character's reference set, and for a
  // character which of its two picture choices the run takes.
  const trainDialogOpen = ref(false);
  const scopeType = ref(null); // the entry: "set" | "character" | null
  const scopeId = ref(null); // numeric set_id or character_id
  const scopeName = ref(""); // the entry's display name
  const dataset = ref(null); // GET /training/dataset
  const datasetLoading = ref(false);
  const datasetError = ref("");
  // A character's pictures: "reference" | "all", or "set" for a set under the
  // person (#2124), which `childSet` keeps across the dataset re-reads.
  const trainOn = ref("all");
  const childSet = ref(null); // { id, name, count } or null
  // Opened from the Models workspace, which has no set or person in view: the
  // dialog leads with a Train on picker, and Start lands on Training runs.
  const pickable = ref(false);

  // ── Dialog: the choices, and what they resolve to ──────────────────────────
  const presets = ref([]); // GET /training/presets: { id, label, offered, … }
  const presetsLoading = ref(false);
  // Recipe updates from pixlstash.dev: the opt-in (null until asked) and
  // the last check's outcome, a sentence for the dialog.
  const presetsAutoCheck = ref(null);
  const presetCheckBusy = ref(false);
  const presetCheckMessage = ref("");
  const selectedPreset = ref(null); // recipe id
  const triggerWord = ref("");
  // What the LoRA learns; a character is always a person, a set must choose.
  const subject = ref(null); // "person" | "style" | "thing"
  const effort = ref("standard"); // "gentle" | "standard" | "strong"
  const fit = ref("auto"); // "auto" | "save_memory" | "best_quality"
  // Typed Advanced values, keyed by tunable setting. A pin holds while the
  // choices change; one equal to the derived value is no pin at all.
  const pins = ref({});
  // Sample prompts as editable lines ({ text, alt }); the written ones are
  // kept so an edited set is a pin and a reset can go back.
  const defaultPrompts = ref([]);
  const prompts = ref([]);
  // What the written prompts were written from: the person's description,
  // the stranger, and how many captions use the description (keep_generic).
  const promptsInfo = ref(null);
  const plan = ref(null); // POST /training/resolve
  const planLoading = ref(false);

  const submitting = ref(false);
  const submitError = ref("");

  // ── Jobs (settings section) ──────────────────────────────────────
  const jobs = ref([]);
  const jobsLoading = ref(false);
  const jobsError = ref("");

  // ── Setup config (ai-toolkit paths) ─────────────────────────────────────────
  const aiToolkitRepoPath = ref("");
  const aiToolkitPython = ref("");
  // Saved or not: a remote owner is told this without the two host paths (#2295).
  const aiToolkitSaved = ref(false);
  const configLoaded = ref(false);

  // ── ai-toolkit setup flow: the last discover, check and status replies ─────
  const aiToolkitDiscovery = ref(null); // { found, repo_path, python, version, searched }
  const aiToolkitCheck = ref(null); // { ok, version, torch, cuda, gpus, trial_ok, detail }
  const aiToolkitStatus = ref(null); // { configured, repo_path, python, version, datasets, runs }

  // ── Deep-link request: open Settings on the Training entry ─────────────────
  const settingsTabRequest = ref(0); // bump to request; App.vue watches

  // ── Derived ─────────────────────────────────────────────────────────────────
  const aiToolkitConfigured = computed(() => aiToolkitSaved.value);

  const hasRunningJob = computed(() => jobs.value.some(isBusyJob));

  /** The character the run is filed under, or null for an ordinary set. */
  const character = computed(() => dataset.value?.character || null);
  /** Whether the chosen recipe trains on the set's video clips too. */
  const presetClips = computed(
    () => !!presets.value.find((p) => p.id === selectedPreset.value)?.clips,
  );
  /** What the run trains on: the stills, and the clips when the recipe takes them. */
  const pictureCount = computed(
    () =>
      (dataset.value?.count ?? 0) +
      (presetClips.value ? dataset.value?.clips ?? 0 : 0),
  );
  const displayName = computed(
    () => character.value?.name || scopeName.value || "",
  );
  /** A character, or a character's reference set, always learns a person. */
  const effectiveSubject = computed(() =>
    character.value ? "person" : subject.value,
  );

  /** Where the pictures come from, as every training route takes it. */
  const scopePayload = computed(() => {
    const ch = character.value;
    if (ch) {
      if (trainOn.value === "set" && childSet.value) {
        return { set_id: childSet.value.id, character_id: ch.id };
      }
      if (trainOn.value === "reference" && ch.reference_set_id != null) {
        return { set_id: ch.reference_set_id, character_id: ch.id };
      }
      return { character_id: ch.id };
    }
    if (scopeType.value === "set") return { set_id: scopeId.value };
    if (scopeType.value === "character") return { character_id: scopeId.value };
    return {};
  });

  /** Whether the owner's prompt lines differ from the written ones. */
  const promptsPinned = computed(
    () =>
      defaultPrompts.value.length > 0 &&
      promptsEdited(prompts.value, defaultPrompts.value),
  );

  /** Every pin Start and the preview send: typed values and edited prompts. */
  const allPins = computed(() => {
    const out = { ...pins.value };
    if (promptsPinned.value) out[PROMPTS_KEY] = promptTexts(prompts.value);
    return out;
  });

  // ── Dialog actions ───────────────────────────────────────────────────────────

  /**
   * Seed the dialog for a fresh run on the given entry and open it. The
   * trigger word starts from the name ("Pub nights" → "pubnights"). Then the
   * dataset is read; a character with a reference set trains on it.
   */
  let openSeq = 0;
  async function openForScope({ type, id, name }) {
    const seq = ++openSeq;
    pickable.value = false;
    scopeType.value = type;
    scopeId.value = id != null ? Number(id) : null;
    scopeName.value = name || "";
    triggerWord.value = triggerFromName(name);
    subject.value = type === "character" ? "person" : null;
    effort.value = "standard";
    fit.value = "auto";
    trainOn.value = "all";
    childSet.value = null;
    pins.value = {};
    defaultPrompts.value = [];
    promptsInfo.value = null;
    prompts.value = [];
    dataset.value = null;
    plan.value = null;
    submitError.value = "";
    trainDialogOpen.value = true;

    presetCheckMessage.value = "";
    fetchPresets();
    (configLoaded.value ? Promise.resolve() : fetchConfig()).then(() => {
      // The opt-in daily check; the server skips it when one ran today.
      if (presetsAutoCheck.value === true) checkPresetUpdates(true);
    });
    const entry =
      type === "character" ? { character_id: scopeId.value } : { set_id: scopeId.value };
    await loadDataset(entry);
    // Closed or reopened on something else while the read was out.
    if (seq !== openSeq || !trainDialogOpen.value) return;
    const ch = character.value;
    if (!ch) {
      // A set under another set is a concept's dataset (#2124): it starts on
      // "A thing", and the owner can still say it is a style.
      if (dataset.value?.parent_set_id != null && subject.value == null) {
        subject.value = "thing";
      }
      return;
    }
    // Opened from a reference set, a set under a person, or a person: the
    // trigger word is the person's name every time.
    triggerWord.value = triggerFromName(ch.name);
    if (ch.child_set) {
      // A set under the person: a character LoRA of them, trained on the set.
      childSet.value = ch.child_set;
      trainOn.value = "set";
    } else if (dataset.value.set_id != null) {
      trainOn.value = "reference";
    } else if (ch.reference_count > 0) {
      trainOn.value = "reference";
      await loadDataset(scopePayload.value);
    }
  }

  /** Open with nothing chosen yet, on the Train on picker (#2169). */
  function openToPick() {
    openSeq += 1;
    resolveSoon.cancel();
    promptsSoon.cancel();
    scopeType.value = null;
    scopeId.value = null;
    scopeName.value = "";
    triggerWord.value = "";
    subject.value = null;
    effort.value = "standard";
    fit.value = "auto";
    trainOn.value = "all";
    pins.value = {};
    defaultPrompts.value = [];
    promptsInfo.value = null;
    prompts.value = [];
    dataset.value = null;
    datasetError.value = "";
    plan.value = null;
    submitError.value = "";
    pickable.value = true;
    trainDialogOpen.value = true;
    fetchPresets();
    if (!configLoaded.value) fetchConfig();
  }

  /** The picker's choice: the dialog reads it as if opened on it. */
  function pickScope(entry) {
    const opened = openForScope(entry);
    pickable.value = true;
    return opened;
  }

  function closeDialog() {
    trainDialogOpen.value = false;
    openSeq += 1;
    resolveSoon.cancel();
    promptsSoon.cancel();
  }

  let datasetSeq = 0;
  async function loadDataset(params) {
    const seq = ++datasetSeq;
    datasetLoading.value = true;
    datasetError.value = "";
    try {
      const data = await getTrainingDataset(params);
      if (seq === datasetSeq) dataset.value = data;
    } catch (err) {
      if (seq !== datasetSeq) return;
      datasetError.value = errorMessage(err, "Could not read the pictures to train on.");
    } finally {
      if (seq === datasetSeq) datasetLoading.value = false;
    }
  }

  /** Switch a character's run between its reference set and every picture. */
  async function setTrainOn(value) {
    if (trainOn.value === value) return;
    trainOn.value = value;
    await loadDataset(scopePayload.value);
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
      // Non-fatal for the dialog; the preview still resolves the default.
      console.warn("Failed to load training recipes:", err);
    } finally {
      presetsLoading.value = false;
    }
  }

  /**
   * Ask the server to check pixlstash.dev for newer recipes. A recipe it
   * replaced is listed again and the preview re-resolved with it. The
   * automatic check speaks only when it changed something.
   */
  async function checkPresetUpdates(automatic = false) {
    if (presetCheckBusy.value) return;
    presetCheckBusy.value = true;
    if (!automatic) presetCheckMessage.value = "";
    try {
      const result = await checkTrainingPresets(automatic);
      const updated = result?.updated || [];
      if (updated.length) {
        await fetchPresets();
        if (trainDialogOpen.value) resolveSoon();
        const names = updated.map((u) => `${u.label} (version ${u.version})`);
        presetCheckMessage.value = `Updated ${names.join(", ")}.`;
      } else if (!automatic) {
        presetCheckMessage.value = result?.error
          ? `${result.error} Your recipes are unchanged.`
          : "Your recipes are up to date.";
      }
    } catch (err) {
      console.warn("Recipe update check failed:", err);
      if (!automatic) {
        presetCheckMessage.value =
          "Could not check for recipe updates. Your recipes are unchanged.";
      }
    } finally {
      presetCheckBusy.value = false;
    }
  }

  /** Turn the daily recipe check on or off (Settings › AI-toolkit). */
  async function setPresetsAutoCheck(value) {
    await patchUserConfig({ training_presets_auto_check: value });
    presetsAutoCheck.value = value;
  }

  let promptsSeq = 0;
  /**
   * Write the default sample prompts for the scope, subject and trigger
   * word. Lines the owner has not edited follow them; an edited set stays.
   */
  async function loadPrompts() {
    const seq = ++promptsSeq;
    if (!effectiveSubject.value || !dataset.value) return;
    try {
      const data = await getTrainingSamplePrompts({
        ...scopePayload.value,
        subject: effectiveSubject.value,
        trigger_word: triggerWord.value.trim() || undefined,
      });
      if (seq !== promptsSeq) return;
      const edited = promptsPinned.value;
      const info = { ...(data || {}) };
      delete info.prompts;
      promptsInfo.value = info;
      defaultPrompts.value = promptLines(data?.prompts);
      if (!edited) prompts.value = defaultPrompts.value.map((l) => ({ ...l }));
    } catch (err) {
      console.warn("Failed to write the sample prompts:", err);
    }
  }

  /** The choices a plan answers, pins aside: a stale plan answers others. */
  const choicesKey = computed(() =>
    JSON.stringify([
      selectedPreset.value,
      effectiveSubject.value,
      effort.value,
      fit.value,
      pictureCount.value,
    ]),
  );

  let planSeq = 0;
  /** Ask the server what the run would use now. */
  async function resolvePlan() {
    const seq = ++planSeq;
    if (!effectiveSubject.value || !dataset.value) {
      plan.value = null;
      return;
    }
    planLoading.value = true;
    const key = choicesKey.value;
    try {
      const body = {
        ...scopePayload.value,
        subject: effectiveSubject.value,
        effort: effort.value,
        fit: fit.value,
        picture_count: pictureCount.value,
      };
      // The captions decide whether the description is kept generic.
      const word = triggerWord.value.trim();
      if (word) body.trigger_word = word;
      if (selectedPreset.value) body.preset = selectedPreset.value;
      if (Object.keys(allPins.value).length) body.pins = allPins.value;
      const data = await resolveTraining(body);
      if (seq === planSeq) plan.value = { ...data, choicesKey: key };
    } catch (err) {
      if (seq !== planSeq) return;
      const detail = err?.response?.data?.detail;
      plan.value = {
        error: {
          message:
            (typeof detail === "string" ? detail : "") ||
            err?.message ||
            "Could not work out the run's settings.",
        },
      };
    } finally {
      if (seq === planSeq) planLoading.value = false;
    }
  }

  function debounced(fn, ms) {
    let timer = null;
    const run = () => {
      clearTimeout(timer);
      timer = setTimeout(fn, ms);
    };
    run.cancel = () => clearTimeout(timer);
    return run;
  }
  const resolveSoon = debounced(resolvePlan, RESOLVE_DEBOUNCE_MS);
  const promptsSoon = debounced(loadPrompts, PROMPTS_DEBOUNCE_MS);

  watch(
    () => [
      trainDialogOpen.value,
      selectedPreset.value,
      effectiveSubject.value,
      effort.value,
      fit.value,
      pictureCount.value,
      dataset.value,
      JSON.stringify(allPins.value),
      JSON.stringify(scopePayload.value),
      triggerWord.value.trim(),
    ],
    () => {
      if (trainDialogOpen.value) resolveSoon();
    },
  );
  // The clips' sound is asked only of a clip recipe; switching to one that
  // trains on stills drops the answer instead of sending it unseen.
  watch(presetClips, (clips) => {
    if (!clips && AUDIO_KEY in pins.value) unpin([AUDIO_KEY]);
  });
  watch(
    () => [trainDialogOpen.value, effectiveSubject.value, dataset.value],
    () => {
      if (trainDialogOpen.value) loadPrompts();
    },
  );
  watch(triggerWord, () => {
    if (trainDialogOpen.value) promptsSoon();
  });

  /**
   * Type over a derived value. Equal to what the choices give (or blank), it
   * is no pin; anything else holds while the choices change. Only a plan for
   * the current choices says what they give: until the debounced resolve
   * answers a change, the value typed is kept as a pin.
   */
  function setPin(key, value) {
    const next = { ...pins.value };
    const current = plan.value?.choicesKey === choicesKey.value;
    const derived = current ? plan.value?.derived?.[key] : null;
    if (value === null || value === undefined || (derived && sameValue(value, derived.value))) {
      delete next[key];
    } else {
      next[key] = value;
    }
    pins.value = next;
  }

  /** Let pinned values follow their choices again. */
  function unpin(keys) {
    const next = { ...pins.value };
    let dropPrompts = false;
    for (const key of keys) {
      if (key === PROMPTS_KEY) dropPrompts = true;
      delete next[key];
    }
    pins.value = next;
    if (dropPrompts) resetPrompts();
  }

  function resetPrompts() {
    prompts.value = defaultPrompts.value.map((l) => ({ ...l }));
  }

  function setPromptText(index, display) {
    const next = [...prompts.value];
    next[index] = { text: promptFromDisplay(display, triggerWord.value), alt: null };
    prompts.value = next;
  }

  function togglePromptTrigger(index) {
    const next = [...prompts.value];
    next[index] = toggleTrigger(next[index]);
    prompts.value = next;
  }

  function removePrompt(index) {
    prompts.value = prompts.value.filter((_, i) => i !== index);
  }

  function addPrompt() {
    prompts.value = [...prompts.value, { text: "", alt: null }];
  }

  function promptDisplay(line) {
    return displayPrompt(line.text, triggerWord.value.trim());
  }

  /**
   * Submit POST /training/jobs from the dialog: the scope, the recipe, the
   * choices and every pin, exactly what the preview resolved. Returns the
   * created job; throws (with the API detail in submitError) on failure.
   */
  async function startTraining() {
    submitting.value = true;
    submitError.value = "";
    try {
      // CreateJobRequest forbids extra fields. The base model is the
      // recipe's, read by the server.
      const trigger = triggerWord.value.trim();
      const model = presets.value.find((p) => p.id === selectedPreset.value)?.label;
      const existing = await listTrainingJobs();
      const body = {
        ...scopePayload.value,
        name: runName(
          displayName.value,
          model,
          (Array.isArray(existing) ? existing : []).map((j) => j.name),
        ),
        trigger_word: trigger,
        effort: effort.value,
        fit: fit.value,
      };
      if (selectedPreset.value) body.preset = selectedPreset.value;
      if (effectiveSubject.value) body.subject = effectiveSubject.value;
      if (Object.keys(allPins.value).length) body.pins = allPins.value;
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

  // ── Registry / jobs actions ───────────────────────────────────────────────────

  async function fetchJobs() {
    jobsLoading.value = true;
    jobsError.value = "";
    try {
      const data = await listTrainingJobs();
      jobs.value = Array.isArray(data) ? data : [];
    } catch (err) {
      jobsError.value = errorMessage(err, "Failed to load training jobs.");
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
      aiToolkitSaved.value = !!config?.ai_toolkit_configured;
      presetsAutoCheck.value = config?.training_presets_auto_check ?? null;
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
    // What the server stored (it trims, and may clear a blank value).
    const saved = await putAiToolkitConfig({ repo_path: repoPath, python });
    aiToolkitRepoPath.value = saved?.repo_path || "";
    aiToolkitPython.value = saved?.python || "";
    aiToolkitSaved.value = !!(saved?.repo_path && saved?.python);
  }

  /** Forget both paths; the install's check and status go with them. */
  async function disconnectAiToolkit() {
    await putAiToolkitConfig({ repo_path: null, python: null });
    aiToolkitRepoPath.value = "";
    aiToolkitPython.value = "";
    aiToolkitSaved.value = false;
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
  // opens the title bar's task list, where the run's progress is.
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
    presetsAutoCheck.value = null;
    presetCheckMessage.value = "";
    aiToolkitRepoPath.value = "";
    aiToolkitPython.value = "";
    aiToolkitSaved.value = false;
    configLoaded.value = false;
    aiToolkitDiscovery.value = null;
    aiToolkitCheck.value = null;
    aiToolkitStatus.value = null;
    closeDialog();
    pickable.value = false;
    scopeType.value = null;
    scopeId.value = null;
    scopeName.value = "";
    trainOn.value = "all";
    childSet.value = null;
    dataset.value = null;
    plan.value = null;
    pins.value = {};
    prompts.value = [];
    defaultPrompts.value = [];
    promptsInfo.value = null;
  }

  const unsubscribeSessionReset = onSessionReset(resetForSession);
  onScopeDispose(() => {
    unsubscribeSessionReset();
    // Closed, a disposed store's pending and in-flight work asks the server
    // nothing later (#2220).
    closeDialog();
  });

  return {
    // dialog: scope
    trainDialogOpen,
    scopeType,
    scopeId,
    scopeName,
    dataset,
    datasetLoading,
    datasetError,
    trainOn,
    childSet,
    pickable,
    openToPick,
    pickScope,
    character,
    presetClips,
    pictureCount,
    displayName,
    scopePayload,
    openForScope,
    closeDialog,
    setTrainOn,
    // dialog: choices
    presets,
    presetsLoading,
    presetsAutoCheck,
    presetCheckBusy,
    presetCheckMessage,
    checkPresetUpdates,
    setPresetsAutoCheck,
    selectedPreset,
    triggerWord,
    subject,
    effectiveSubject,
    effort,
    fit,
    pins,
    choicesKey,
    allPins,
    setPin,
    unpin,
    plan,
    planLoading,
    defaultPrompts,
    prompts,
    promptsInfo,
    promptsPinned,
    resetPrompts,
    setPromptText,
    togglePromptTrigger,
    removePrompt,
    addPrompt,
    promptDisplay,
    submitting,
    submitError,
    fetchPresets,
    resolvePlan,
    loadPrompts,
    startTraining,
    // jobs
    jobs,
    jobsLoading,
    jobsError,
    hasRunningJob,
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
