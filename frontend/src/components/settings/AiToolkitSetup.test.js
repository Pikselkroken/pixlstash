// Settings › AI-toolkit, local install: find the checkout, check it can train,
// connect, the Ready card, Disconnect. Mocked API; the store is real.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", () => ({
  VIcon: { template: "<i />" },
  VProgressCircular: { template: "<i />" },
}));

const training = vi.hoisted(() => ({
  discoverAiToolkit: vi.fn(),
  checkAiToolkit: vi.fn(),
  getAiToolkitStatus: vi.fn(),
  cancelTrainingJob: vi.fn(),
  createTrainingJob: vi.fn(),
  deleteTrainingJob: vi.fn(),
  listTrainingJobs: vi.fn(),
  listTrainingTemplates: vi.fn(),
  putAiToolkitConfig: vi.fn(),
}));
vi.mock("../../api/training", () => training);

const config = vi.hoisted(() => ({
  getUserConfig: vi.fn(),
}));
vi.mock("../../api/config", () => config);

import AiToolkitSetup from "./AiToolkitSetup.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";

const REPO = "/home/me/ai-toolkit";
const PY = "/home/me/ai-toolkit/.venv/bin/python";

const FOUND = {
  found: true,
  repo_path: REPO,
  python: PY,
  version: "0.13.34",
  searched: [REPO],
};
const NOT_FOUND = {
  found: false,
  repo_path: null,
  python: null,
  version: null,
  searched: ["/home/me/ai-toolkit", "/home/me/Projects/ai-toolkit"],
};
const ONE_GPU = [{ name: "RTX 4090", total_mb: 24576 }];
const DATASETS = { path: "/home/me/ai-toolkit/datasets", writable: true };
const RUNS = {
  path: "/home/me/ai-toolkit/output",
  run_count: 3,
  on_shelf: true,
};
const CHECK_OK = {
  ok: true,
  version: "0.13.34",
  torch: "2.13",
  cuda: true,
  gpus: ONE_GPU,
  trial_ok: true,
  detail: "",
  python: PY,
  datasets: DATASETS,
  runs: RUNS,
  presets: [
    {
      id: "zimage-turbo",
      label: "Z-Image Turbo",
      requires_arch: "z_image",
      offered: true,
    },
  ],
};
const STATUS = {
  configured: true,
  repo_path: REPO,
  python: PY,
  version: "0.13.34",
  datasets: DATASETS,
  runs: RUNS,
};
const SENTENCE_ONE_GPU =
  "Its venv has torch 2.13 with CUDA and sees one GPU with 24 GB. A trial start loaded the trainer and exited without training.";

const NOT_CONNECTED = { ai_toolkit_repo_path: null, ai_toolkit_python: null };
const CONNECTED = { ai_toolkit_repo_path: REPO, ai_toolkit_python: PY };
// The PUT body: the checkout and its Python, or both null to disconnect.
const SAVED = { repo_path: REPO, python: PY };
const CLEARED = { repo_path: null, python: null };

const wrappers = [];

function httpError(status, detail) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data: { detail } },
  });
}

async function mountSetup() {
  const wrapper = mount(AiToolkitSetup, {
    props: { open: true },
    attachTo: document.body,
    global: {
      stubs: {
        FolderBrowser: {
          props: ["open"],
          emits: ["select", "close"],
          template: "<div />",
        },
      },
    },
  });
  wrappers.push(wrapper);
  await flushPromises();
  return wrapper;
}

const byTestId = (wrapper, id) => wrapper.find(`[data-testid="${id}"]`);
const connectButton = (w) => byTestId(w, "ait-connect");
const liveRegion = (w) => w.find('[role="status"]').text();

beforeEach(() => {
  setActivePinia(createPinia());
  Object.values(training).forEach((fn) => fn.mockReset());
  Object.values(config).forEach((fn) => fn.mockReset());
  config.getUserConfig.mockResolvedValue(NOT_CONNECTED);
  training.discoverAiToolkit.mockResolvedValue(FOUND);
  training.checkAiToolkit.mockResolvedValue(CHECK_OK);
  training.getAiToolkitStatus.mockResolvedValue(STATUS);
});

afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  document.body.innerHTML = "";
});

describe("when no checkout is found", () => {
  it("offers a folder input and Search again, and never Connect", async () => {
    training.discoverAiToolkit.mockResolvedValue(NOT_FOUND);
    const w = await mountSetup();

    expect(w.text()).toContain("AI-toolkit not found on this computer");
    expect(w.text()).toContain("/home/me/Projects/ai-toolkit");
    expect(byTestId(w, "ait-folder-input").find("input").exists()).toBe(true);
    expect(byTestId(w, "ait-search-again").exists()).toBe(true);
    expect(connectButton(w).exists()).toBe(false);
    expect(training.checkAiToolkit).not.toHaveBeenCalled();
  });
});

describe("when a checkout is found", () => {
  it("checks it at once and reads the sentence back", async () => {
    const w = await mountSetup();

    expect(training.checkAiToolkit).toHaveBeenCalledWith({
      repo_path: REPO,
      python: PY,
    });
    expect(byTestId(w, "ait-check-sentence").text()).toBe(SENTENCE_ONE_GPU);
    expect(connectButton(w).exists()).toBe(true);
    expect(liveRegion(w)).toContain("AI-toolkit can train.");
  });

  it("says which recipes the checkout can train", async () => {
    const w = await mountSetup();
    expect(byTestId(w, "ait-recipes-sentence").text()).toBe(
      "It can train Z-Image Turbo.",
    );
  });

  it("says a checkout without the model code cannot train the recipes", async () => {
    training.checkAiToolkit.mockResolvedValue({
      ...CHECK_OK,
      presets: [{ ...CHECK_OK.presets[0], offered: false }],
    });
    const w = await mountSetup();
    const sentence = byTestId(w, "ait-recipes-sentence").text();
    expect(sentence).toContain("(z_image)");
    expect(sentence).toContain("cannot train any of them yet");
  });

  it("pluralises the GPUs and says each when they match", async () => {
    training.checkAiToolkit.mockResolvedValue({
      ...CHECK_OK,
      gpus: [
        { name: "a", total_mb: 24576 },
        { name: "b", total_mb: 24576 },
      ],
    });
    const w = await mountSetup();

    expect(byTestId(w, "ait-check-sentence").text()).toContain(
      "sees two GPUs with 24 GB each.",
    );
  });

  it("rounds the memory to whole GB", async () => {
    training.checkAiToolkit.mockResolvedValue({
      ...CHECK_OK,
      gpus: [{ name: "a", total_mb: 24500 }],
    });
    const w = await mountSetup();

    expect(byTestId(w, "ait-check-sentence").text()).toContain(
      "sees one GPU with 24 GB.",
    );
  });

  it("treats a trial that did not run as a failure even when ok is set", async () => {
    training.checkAiToolkit.mockResolvedValue({
      ...CHECK_OK,
      trial_ok: false,
      detail: "The trainer failed to load.",
    });
    const w = await mountSetup();

    expect(connectButton(w).exists()).toBe(false);
    expect(byTestId(w, "ait-check-sentence").exists()).toBe(false);
    expect(byTestId(w, "ait-check-error").text()).toContain(
      "The trainer failed to load.",
    );
  });

  it("shows the check's detail and offers no Connect when it fails", async () => {
    training.checkAiToolkit.mockResolvedValue({
      ...CHECK_OK,
      ok: false,
      detail: "The trial start failed: no module named trainer.",
    });
    const w = await mountSetup();

    expect(byTestId(w, "ait-check-error").text()).toContain(
      "The trial start failed: no module named trainer.",
    );
    expect(connectButton(w).exists()).toBe(false);
    expect(liveRegion(w)).toContain("AI-toolkit cannot train here.");
  });

  it("shows the server's detail when the check is refused with 422", async () => {
    training.checkAiToolkit.mockRejectedValue(
      httpError(422, "Not an ai-toolkit checkout: run.py is missing."),
    );
    const w = await mountSetup();

    expect(byTestId(w, "ait-check-error").text()).toContain(
      "Not an ai-toolkit checkout: run.py is missing.",
    );
    expect(connectButton(w).exists()).toBe(false);
  });
});

describe("the folders, before Connect", () => {
  it("shows the Folders from the check before anything is saved", async () => {
    const w = await mountSetup();

    expect(byTestId(w, "ait-folders").exists()).toBe(true);
    expect(byTestId(w, "ait-datasets-writable").text()).toBe("writable");
    expect(byTestId(w, "ait-runs-count").text()).toBe("3 runs");
    expect(training.putAiToolkitConfig).not.toHaveBeenCalled();
    expect(connectButton(w).exists()).toBe(true);
  });

  it("says the runs will join the shelf when the folder is not on it yet", async () => {
    training.checkAiToolkit.mockResolvedValue({
      ...CHECK_OK,
      runs: { ...RUNS, on_shelf: false },
    });
    const w = await mountSetup();

    expect(byTestId(w, "ait-shelf-line").text()).toBe(
      "Runs land in your model shelf's ai-toolkit folder. PixlStash adds this folder to the shelf when the first run starts.",
    );
  });
});

describe("the python Connect saves", () => {
  it("is the one the check resolved, not the one discovery gave", async () => {
    training.discoverAiToolkit.mockResolvedValue({
      ...FOUND,
      python: "/usr/bin/python3",
    });
    const w = await mountSetup();
    await connectButton(w).trigger("click");
    await flushPromises();

    expect(training.putAiToolkitConfig).toHaveBeenCalledWith(SAVED);
  });

  it("lets a hand-picked folder connect once the check resolves its python", async () => {
    training.discoverAiToolkit.mockResolvedValue(NOT_FOUND);
    const w = await mountSetup();
    training.checkAiToolkit.mockResolvedValue({
      ...CHECK_OK,
      python: "/srv/ai-toolkit/.venv/bin/python",
    });
    w.findComponent(FolderBrowser).vm.$emit("select", "/srv/ai-toolkit");
    await flushPromises();

    expect(training.checkAiToolkit).toHaveBeenLastCalledWith({
      repo_path: "/srv/ai-toolkit",
      python: null,
    });
    expect(connectButton(w).attributes("disabled")).toBeUndefined();
    await connectButton(w).trigger("click");
    await flushPromises();
    expect(training.putAiToolkitConfig).toHaveBeenCalledWith({
      repo_path: "/srv/ai-toolkit",
      python: "/srv/ai-toolkit/.venv/bin/python",
    });
  });

  it("is not offered when the check resolves no python", async () => {
    training.discoverAiToolkit.mockResolvedValue(NOT_FOUND);
    training.checkAiToolkit.mockResolvedValue({ ...CHECK_OK, python: null });
    const w = await mountSetup();
    w.findComponent(FolderBrowser).vm.$emit("select", "/srv/ai-toolkit");
    await flushPromises();

    expect(byTestId(w, "ait-no-python").exists()).toBe(true);
    expect(connectButton(w).attributes("disabled")).toBeDefined();
  });
});

describe("connecting", () => {
  it("saves both paths in one PATCH and shows the Ready card", async () => {
    const w = await mountSetup();
    await connectButton(w).trigger("click");
    await flushPromises();

    expect(training.putAiToolkitConfig).toHaveBeenCalledTimes(1);
    expect(training.putAiToolkitConfig).toHaveBeenCalledWith(SAVED);
    expect(w.text()).toContain("Ready to train");
    expect(w.text()).toContain("AI-toolkit 0.13.34");
  });

  it("shows the server's detail when the save is refused with 403", async () => {
    training.putAiToolkitConfig.mockRejectedValue(
      httpError(403, "Only the owner of this library can change AI-toolkit."),
    );
    const w = await mountSetup();
    await connectButton(w).trigger("click");
    await flushPromises();

    expect(byTestId(w, "ait-connect-error").text()).toContain(
      "Only the owner of this library can change AI-toolkit.",
    );
    expect(w.text()).not.toContain("Ready to train");
    expect(connectButton(w).exists()).toBe(true);
  });

  it("moves focus to Check again once connected", async () => {
    const w = await mountSetup();
    await connectButton(w).trigger("click");
    await flushPromises();

    expect(document.activeElement).toBe(byTestId(w, "ait-check-again").element);
  });
});

describe("when connected", () => {
  beforeEach(() => {
    config.getUserConfig.mockResolvedValue(CONNECTED);
  });

  it("shows the Ready card from the check and the status", async () => {
    const w = await mountSetup();

    expect(training.discoverAiToolkit).not.toHaveBeenCalled();
    expect(w.text()).toContain("Ready to train");
    expect(w.text()).toContain("AI-toolkit 0.13.34");
    expect(w.text()).toContain("On this computer");
    expect(w.text()).toContain("starting the trainer itself");
    expect(w.text()).toContain("CUDA");
    expect(byTestId(w, "ait-datasets-writable").text()).toBe("writable");
    expect(byTestId(w, "ait-runs-count").text()).toBe("3 runs");
    expect(byTestId(w, "ait-shelf-line").text()).toBe(
      "Runs land in your model shelf's ai-toolkit folder.",
    );
  });

  it("says so when the datasets folder is not writable", async () => {
    training.getAiToolkitStatus.mockResolvedValue({
      ...STATUS,
      datasets: { path: "/ro/datasets", writable: false },
    });
    const w = await mountSetup();

    expect(byTestId(w, "ait-datasets-writable").text()).toBe("not writable");
  });

  it("re-runs the check on Check again", async () => {
    const w = await mountSetup();
    training.checkAiToolkit.mockClear();
    await byTestId(w, "ait-check-again").trigger("click");
    await flushPromises();

    expect(training.checkAiToolkit).toHaveBeenCalledWith({
      repo_path: REPO,
      python: PY,
    });
  });

  it("Disconnect clears both paths and returns to the find flow", async () => {
    const w = await mountSetup();
    await byTestId(w, "ait-disconnect").trigger("click");
    await flushPromises();

    expect(training.putAiToolkitConfig).toHaveBeenCalledWith(CLEARED);
    expect(training.discoverAiToolkit).toHaveBeenCalledTimes(1);
    expect(w.text()).not.toContain("Ready to train");
    expect(connectButton(w).exists()).toBe(true);
  });

  it("moves focus to Connect after Disconnect", async () => {
    const w = await mountSetup();
    await byTestId(w, "ait-disconnect").trigger("click");
    await flushPromises();

    expect(document.activeElement).toBe(connectButton(w).element);
  });
});

describe("choosing a folder", () => {
  it("checks the folder the browser picked", async () => {
    training.discoverAiToolkit.mockResolvedValue(NOT_FOUND);
    const w = await mountSetup();
    w.findComponent(FolderBrowser).vm.$emit("select", "/srv/ai-toolkit");
    await flushPromises();

    expect(training.checkAiToolkit).toHaveBeenCalledWith({
      repo_path: "/srv/ai-toolkit",
      python: null,
    });
  });
});
