// What a pre-flight answer offers for download, and starting those once each.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import {
  downloadableInPreflight,
  downloadModels,
} from "./useModelDownloads";
import { startModelDownload } from "../api/modelDownloads";
import { useNoticeStore } from "../stores/useNoticeStore";
import { useTasksStore } from "../stores/useTasksStore";

vi.mock("../api/modelDownloads", () => ({
  startModelDownload: vi.fn(),
  cancelModelDownload: vi.fn(),
}));
vi.mock("../api/workers", () => ({ getWorkerProgress: vi.fn() }));

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe("downloadableInPreflight", () => {
  it("takes linked missing models and left-out LoRAs, not the owner's skips", () => {
    const answer = {
      groups: [
        {
          reasons: [
            {
              code: "missing_models",
              models: [
                { file: "vae/a.safetensors", downloadable: true },
                { file: "b.safetensors" },
                { downloadable: true },
              ],
            },
            { code: "missing_nodes", models: [{ file: "c.safetensors", downloadable: true }] },
          ],
          bypassed_loras: [
            { file: "lora-d.safetensors", downloadable: true },
            { file: "lora-e.safetensors", downloadable: true, requested: true },
            { file: "lora-f.safetensors", downloadable: true, recipe: true },
            { file: "lora-g.safetensors" },
          ],
        },
      ],
    };
    expect(downloadableInPreflight(answer)).toEqual([
      "vae/a.safetensors",
      "lora-d.safetensors",
    ]);
    expect(downloadableInPreflight(null)).toEqual([]);
  });
});

describe("downloadModels", () => {
  it("starts each name once, skips one already running, and says so once", async () => {
    startModelDownload.mockImplementation(async (file) => ({
      id: file,
      file: file.split("/").pop(),
      status: "running",
    }));
    useTasksStore().downloadStarted({
      id: "x",
      file: "running.safetensors",
      status: "running",
    });
    const started = await downloadModels([
      "vae/a.safetensors",
      "a.safetensors",
      "b.safetensors",
      "running.safetensors",
    ]);
    expect(started).toBe(2);
    expect(startModelDownload.mock.calls.map(([file]) => file)).toEqual([
      "vae/a.safetensors",
      "b.safetensors",
    ]);
    expect(useNoticeStore().notices.map((n) => n.text)).toEqual([
      "Downloading 2 models.",
    ]);
  });

  it("says so when everything asked for is already downloading", async () => {
    useTasksStore().downloadStarted({
      id: "x",
      file: "running.safetensors",
      status: "running",
    });
    expect(await downloadModels(["loras/running.safetensors"])).toBe(0);
    expect(startModelDownload).not.toHaveBeenCalled();
    expect(useNoticeStore().notices.map((n) => n.text)).toEqual([
      "Already downloading: the Tasks tab shows how far.",
    ]);
  });

  it("reports a refusal and still starts the rest", async () => {
    startModelDownload
      .mockRejectedValueOnce({ response: { data: { detail: "No folder." } } })
      .mockResolvedValueOnce({ id: "b", file: "b.safetensors", status: "running" });
    expect(await downloadModels(["a.safetensors", "b.safetensors"])).toBe(1);
    expect(useNoticeStore().notices.map((n) => n.text).sort()).toEqual([
      "Downloading b.safetensors.",
      "No folder.",
    ]);
  });
});
