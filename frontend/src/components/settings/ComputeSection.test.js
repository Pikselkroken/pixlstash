// ComputeSection unified runtime list (compute view):
//   - the built-in runtime is a permanent first row and every GPU overlay is a
//     sibling row - no separate "active backend" summary row, so an active
//     overlay is never listed twice;
//   - exactly one row reads "· active" at any time (each row derives it from
//     its own `active` flag);
//   - rows keep their position when the active runtime changes (only status
//     text and action buttons swap), so the section reads correctly right
//     after a failed GPU activation reverts to the built-in runtime.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn().mockResolvedValue({ data: {} }) },
  login: vi.fn(),
}));

// ComputeSection (and the App* widgets it embeds) import Vuetify components
// directly, which pull in CSS vitest can't load; replace them with stubs.
vi.mock("vuetify/components", () => {
  const stub = (name) => ({ name, template: "<div><slot /></div>" });
  return {
    VSwitch: stub("v-switch"),
    VIcon: { name: "v-icon", template: "<i><slot /></i>" },
    VTooltip: stub("v-tooltip"),
    VProgressLinear: stub("v-progress-linear"),
  };
});

import ComputeSection from "./ComputeSection.vue";

function installDesktop(accelState) {
  const desktop = {
    listAccelerators: vi.fn().mockResolvedValue(accelState),
    getBackendLocation: vi.fn().mockResolvedValue({ dir: "/overlays" }),
    onProgress: vi.fn().mockReturnValue(() => {}),
    useAccelerator: vi.fn().mockResolvedValue(undefined),
    installAccelerator: vi.fn().mockResolvedValue(undefined),
    removeAccelerator: vi.fn().mockResolvedValue(undefined),
  };
  window.pixlstashDesktop = desktop;
  return desktop;
}

async function mountCompute(accelState) {
  const desktop = installDesktop(accelState);
  const wrapper = mount(ComputeSection, { props: { view: "compute" } });
  await flushPromises();
  return { wrapper, desktop };
}

// Helpers to read the SettingsRow-based runtime list.
const rows = (wrapper) => wrapper.findAll(".s-row");
const rowLabel = (row) => row.find(".s-row__label").text();
const rowSub = (row) =>
  row.find(".s-row__sub").exists() ? row.find(".s-row__sub").text() : "";
const rowButtons = (row) => row.findAll("button").map((b) => b.text());

const gpuActiveState = () => ({
  bundled: { accel: "cpu", label: "Built-in (CPU)", active: false },
  items: [
    {
      accel: "cuda",
      label: "NVIDIA GPU (CUDA)",
      installed: true,
      active: true,
      recommended: true,
    },
  ],
});

const bundledActiveState = () => ({
  bundled: { accel: "cpu", label: "Built-in (CPU)", active: true },
  items: [
    {
      accel: "cuda",
      label: "NVIDIA GPU (CUDA)",
      installed: true,
      active: false,
      recommended: true,
    },
  ],
});

describe("ComputeSection unified runtime list", () => {
  beforeEach(() => {
    delete window.pixlstashDesktop;
  });

  it("lists an active GPU overlay exactly once, with the bundled runtime as first row", async () => {
    const { wrapper } = await mountCompute(gpuActiveState());
    const all = rows(wrapper);

    const gpuRows = all.filter((r) => rowLabel(r) === "NVIDIA GPU (CUDA)");
    expect(gpuRows).toHaveLength(1);

    expect(rowLabel(all[0])).toBe("Built-in (CPU)");
    expect(rowSub(all[0])).toBe("Built-in runtime");
    expect(rowSub(gpuRows[0])).toBe("Installed · active");
  });

  it("marks exactly one row active at any time", async () => {
    for (const state of [gpuActiveState(), bundledActiveState()]) {
      const { wrapper } = await mountCompute(state);
      const activeRows = rows(wrapper).filter((r) =>
        rowSub(r).includes("· active"),
      );
      expect(activeRows).toHaveLength(1);
    }
  });

  it("offers Use on the bundled row (not the active overlay) while a GPU is active", async () => {
    const { wrapper, desktop } = await mountCompute(gpuActiveState());
    const all = rows(wrapper);

    expect(rowButtons(all[0])).toEqual(["Use"]);
    const useBtn = all[0].find("button");
    expect(useBtn.attributes("aria-label")).toBe("Use Built-in (CPU)");

    // The active overlay row keeps Remove but has no redundant Use.
    const gpuRow = all.find((r) => rowLabel(r) === "NVIDIA GPU (CUDA)");
    expect(rowButtons(gpuRow)).toEqual(["Remove"]);
    expect(gpuRow.find("button").attributes("aria-label")).toBe(
      "Remove NVIDIA GPU (CUDA)",
    );

    await useBtn.trigger("click");
    expect(desktop.useAccelerator).toHaveBeenCalledWith(null);
  });

  it("reads correctly after a failed GPU activation reverts to the built-in runtime", async () => {
    // Post-reversion state: bundled active again, overlay merely installed.
    const { wrapper, desktop } = await mountCompute(bundledActiveState());
    const all = rows(wrapper);

    // Same row order as the GPU-active state - nothing moved.
    expect(rowLabel(all[0])).toBe("Built-in (CPU)");
    expect(rowSub(all[0])).toBe("Built-in runtime · active");
    expect(rowButtons(all[0])).toEqual([]);

    const gpuRow = all.find((r) => rowLabel(r) === "NVIDIA GPU (CUDA)");
    expect(rowSub(gpuRow)).toBe("Installed");
    expect(rowButtons(gpuRow)).toEqual(["Use", "Remove"]);

    const gpuUse = gpuRow.findAll("button").find((b) => b.text() === "Use");
    expect(gpuUse.attributes("aria-label")).toBe("Use NVIDIA GPU (CUDA)");
    await gpuUse.trigger("click");
    expect(desktop.useAccelerator).toHaveBeenCalledWith("cuda");
  });

  it("offers Install (recommended) for an uninstalled recommended overlay", async () => {
    const { wrapper, desktop } = await mountCompute({
      bundled: { accel: "cpu", label: "Built-in (CPU)", active: true },
      items: [
        {
          accel: "cuda",
          label: "NVIDIA GPU (CUDA)",
          installed: false,
          active: false,
          recommended: true,
        },
      ],
    });
    const gpuRow = rows(wrapper).find(
      (r) => rowLabel(r) === "NVIDIA GPU (CUDA)",
    );
    expect(rowButtons(gpuRow)).toEqual(["Install (recommended)"]);
    const installBtn = gpuRow.find("button");
    expect(installBtn.attributes("aria-label")).toBe(
      "Install NVIDIA GPU (CUDA)",
    );
    await installBtn.trigger("click");
    expect(desktop.installAccelerator).toHaveBeenCalledWith("cuda");
  });
});

// The desktop CLI shim toggle. Its whole job is to be absent where there is
// nothing to install (an unpackaged dev run) - the main process signals that by
// reporting shellCommand as null rather than a boolean, and a switch that
// silently does nothing is worse than no switch.
describe("Desktop › Shell command", () => {
  async function mountBackend(prefs) {
    const desktop = installDesktop({ runtime: null, accelerators: [] });
    desktop.getDesktopPrefs = vi.fn().mockResolvedValue(prefs);
    desktop.setDesktopPrefs = vi.fn().mockResolvedValue(prefs);
    const wrapper = mount(ComputeSection, { props: { view: "backend" } });
    await flushPromises();
    return { wrapper, desktop };
  }

  const shellRow = (wrapper) =>
    rows(wrapper).find((r) => rowLabel(r) === "Shell command");

  it("is hidden where the app has no shim to install", async () => {
    const { wrapper } = await mountBackend({
      hideToTrayOnClose: true,
      shellCommand: null,
    });
    expect(shellRow(wrapper)).toBeUndefined();
  });

  it("offers the toggle, off by default, where a shim can be installed", async () => {
    const { wrapper } = await mountBackend({
      hideToTrayOnClose: true,
      shellCommand: false,
    });
    const row = shellRow(wrapper);
    expect(row).toBeDefined();
    // The stub declares no props, so the bound value arrives as an attr.
    expect(
      row.findComponent({ name: "v-switch" }).vm.$attrs["model-value"],
    ).toBe(false);
  });

  it("names the directory it is about to write, per platform", async () => {
    // The destination differs by platform (#1060 gave Windows a bin directory
    // of its own and put it on PATH), so the row reads it from the main process
    // rather than hardcoding the POSIX one. A PATH change only reaches a shell
    // started after it, which is why the row says to open a new terminal.
    const { wrapper } = await mountBackend({
      hideToTrayOnClose: true,
      shellCommand: false,
      shellCommandDir: "C:\\Users\\me\\AppData\\Local\\PixlStash\\bin",
    });
    const sub = rowSub(shellRow(wrapper));
    expect(sub).toContain("C:\\Users\\me\\AppData\\Local\\PixlStash\\bin");
    expect(sub).toContain("new terminal");
  });

  it("turning it on asks the main process to install the shim", async () => {
    const { wrapper, desktop } = await mountBackend({
      hideToTrayOnClose: true,
      shellCommand: false,
    });
    await shellRow(wrapper)
      .findComponent({ name: "v-switch" })
      .vm.$emit("update:modelValue", true);
    expect(desktop.setDesktopPrefs).toHaveBeenCalledWith({
      shellCommand: true,
    });
  });

  it("snaps back with a reason when the install is refused", async () => {
    // A `pixlstash` the user wrote themselves is never overwritten, so enabling
    // can fail. A switch left sitting on over a command that does not exist is
    // the failure worth avoiding.
    const { wrapper, desktop } = await mountBackend({
      hideToTrayOnClose: true,
      shellCommand: false,
    });
    desktop.setDesktopPrefs.mockRejectedValue(
      new Error("~/.local/bin/pixlstash already exists"),
    );

    await shellRow(wrapper)
      .findComponent({ name: "v-switch" })
      .vm.$emit("update:modelValue", true);
    await flushPromises();

    expect(
      shellRow(wrapper).findComponent({ name: "v-switch" }).vm.$attrs[
        "model-value"
      ],
    ).toBe(false);
    expect(wrapper.text()).toContain("already exists");
  });

  it("follows the state the main process reports, not the one requested", async () => {
    const { wrapper, desktop } = await mountBackend({
      hideToTrayOnClose: true,
      shellCommand: false,
    });
    desktop.setDesktopPrefs.mockResolvedValue({
      hideToTrayOnClose: true,
      shellCommand: true,
    });

    await shellRow(wrapper)
      .findComponent({ name: "v-switch" })
      .vm.$emit("update:modelValue", true);
    await flushPromises();

    expect(
      shellRow(wrapper).findComponent({ name: "v-switch" }).vm.$attrs[
        "model-value"
      ],
    ).toBe(true);
  });
});
