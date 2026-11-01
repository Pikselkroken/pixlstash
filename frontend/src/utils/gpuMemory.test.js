// The pure parts of the GPU-memory surfaces: the figures, the run-id matching
// every reader shares, and where a request sits on a card's bar.

import { describe, expect, it } from "vitest";
import {
  deviceVramBar,
  formatGb,
  normalizeRunId,
  peakBars,
  requestOnBar,
  runKey,
  runKeys,
  runMatches,
} from "./gpuMemory";

const CARD = {
  uuid: "GPU-1",
  name: "RTX 4090",
  total_mb: 24576,
  free_mb: 10240,
  budget_mb: 8192,
};

describe("formatGb", () => {
  it("prints a figure in GB at one decimal", () => {
    expect(formatGb(9800)).toBe("9.6 GB");
    expect(formatGb(14950)).toBe("14.6 GB");
  });

  it("has no figure for a need nobody measured, and never 0 B", () => {
    expect(formatGb(null)).toBe("");
    expect(formatGb(undefined)).toBe("");
    expect(formatGb(0)).toBe("0.0 GB");
  });
});

describe("run ids", () => {
  it("strips the comfyui- prefix a plugin event carries, and nothing else", () => {
    expect(normalizeRunId("comfyui-p-1")).toBe("p-1");
    expect(normalizeRunId("run-abc")).toBe("run-abc");
    expect(normalizeRunId(null)).toBe("");
  });

  it("knows a held run by its run id, which is the only id it has while held", () => {
    const held = { run_id: "run-1", prompt_id: null };
    expect(runKeys(held)).toEqual(["run-1"]);
    expect(runKey(held)).toBe("run-1");
    expect(runMatches(held, "run-1")).toBe(true);
    // A null prompt id is not the string "null".
    expect(runMatches(held, "null")).toBe(false);
  });

  it("matches on either id once the run has been sent", () => {
    const sent = { run_id: "run-1", prompt_id: "p-9" };
    expect(runMatches(sent, "run-1")).toBe(true);
    expect(runMatches(sent, "p-9")).toBe(true);
    expect(runMatches(sent, "comfyui-p-9")).toBe(true);
    expect(runMatches(sent, "p-8")).toBe(false);
    expect(runMatches(sent, "")).toBe(false);
  });

  it("falls back to the prompt id as the key of an answer that predates run ids", () => {
    expect(runKey({ prompt_id: "p-2" })).toBe("p-2");
    expect(runKey({})).toBe("");
  });
});

describe("deviceVramBar", () => {
  it("splits the card into tenants, other apps and the spare margin, and adds up", () => {
    const bar = deviceVramBar(CARD, [
      { device: "GPU-1", tenant: "comfyui", used_mb: 9800, label: "ComfyUI" },
      { device: "GPU-1", tenant: "pixlstash", used_mb: 1000, label: "PixlStash" },
      { device: "GPU-2", tenant: "comfyui", used_mb: 999, label: "elsewhere" },
    ]);
    expect(bar.total).toBe(24576);
    expect(bar.free).toBe(10240);
    const byKind = Object.fromEntries(bar.segments.map((s) => [s.kind, s.mb]));
    expect(byKind.comfyui).toBe(9800);
    expect(byKind.pixlstash).toBe(1000);
    // Used memory no holder claims is other apps'.
    expect(byKind.other).toBe(24576 - 10240 - 9800 - 1000);
    // Free above the budget is the spare margin: 10240 - 8192.
    expect(byKind.spare).toBe(2048);
    // The holder on another card is not counted here.
    expect(Object.keys(byKind)).not.toContain("elsewhere");
  });

  it("draws a tenant's reservation in its own colour, after what is in use, not as spare", () => {
    // 2048 kept back: 1500 of it is AI Toolkit's reservation, the rest spare.
    const bar = deviceVramBar({ ...CARD, reserved_by: { aitoolkit: 1500 } }, []);
    expect(bar.segments.map((s) => [s.key, s.mb])).toEqual([
      ["other", 24576 - 10240],
      ["aitoolkit-reserved", 1500],
      ["spare", 2048 - 1500],
    ]);
    expect(bar.segments[1].reserved).toBe(true);
    expect(bar.segments[1].kind).toBe("aitoolkit");
  });

  it("never draws more reserved than the memory kept back, and drops no tenant", () => {
    // Over-committed: 4096 reserved against 2048 kept back. Both halve.
    const bar = deviceVramBar(
      { ...CARD, reserved_by: { pixlstash: 2048, aitoolkit: 2048 } },
      [],
    );
    const reserved = bar.segments.filter((s) => s.reserved);
    expect(reserved.map((s) => [s.kind, s.mb])).toEqual([
      ["pixlstash", 1024],
      ["aitoolkit", 1024],
    ]);
    expect(bar.segments.map((s) => s.kind)).not.toContain("spare");
  });

  it("draws no spare when a request could get all the free memory", () => {
    const bar = deviceVramBar({ ...CARD, budget_mb: 10240 }, []);
    expect(bar.segments.map((s) => s.kind)).toEqual(["other"]);
  });

  it("has no free memory, and adds up, on a card reporting more used than it has", () => {
    const bar = deviceVramBar({ uuid: "GPU-1", total_mb: 8192, used_mb: 9000 }, []);
    expect(bar.free).toBe(0);
    expect(bar.segments.every((s) => s.mb >= 0)).toBe(true);
    expect(bar.segments.reduce((sum, s) => sum + s.mb, 0)).toBe(8192);
  });

  it("is empty for a card with no figures", () => {
    expect(deviceVramBar({ uuid: "x" }, [])).toEqual({ total: 0, free: 0, segments: [] });
  });
});

describe("peakBars", () => {
  it("scales to the tallest peak and keeps the used and oom flags", () => {
    const bars = peakBars([
      { peak_mb: 8192, pixels: 1048576, outcome: "completed", used: true },
      { peak_mb: 4096, outcome: "oom", used: false },
    ]);
    expect(bars.map((b) => b.pct)).toEqual([100, 50]);
    expect(bars.map((b) => b.pixels)).toEqual([1048576, null]);
    expect(bars.map((b) => b.used)).toEqual([true, false]);
    expect(bars[1].outcome).toBe("oom");
  });

  it("reads no peaks as no bars", () => {
    expect(peakBars(undefined)).toEqual([]);
  });
});

describe("requestOnBar", () => {
  it("starts a request where the free memory starts and sticks out past the card", () => {
    // 24 GB card, 13 GB used, 11 GB free. A 14 GB request starts at the used
    // edge and is 3 GB over the end.
    const device = { uuid: "GPU-1", total_mb: 24576, free_mb: 11264, budget_mb: 11264 };
    const holders = [{ device: "GPU-1", tenant: "pixlstash", used_mb: 13312, label: "PixlStash" }];
    const bar = requestOnBar(device, holders, 14336);
    expect(bar.startPct).toBeCloseTo((13312 / 24576) * 100);
    expect(bar.overMb).toBe(14336 - 11264);
    // The box's full width runs past 100% by exactly its over-part.
    expect(bar.startPct + bar.needPct - 100).toBeCloseTo(bar.overPct);
  });

  it("has no over-part when the request fits the free memory", () => {
    const device = { uuid: "GPU-1", total_mb: 24576, free_mb: 11264, budget_mb: 11264 };
    const bar = requestOnBar(device, [], 4096);
    expect(bar.overMb).toBe(0);
    expect(bar.overPct).toBe(0);
  });
});
