// ThumbnailUpgradeBanner visibility + progress logic, driven from a mocked
// useTasksStore worker snapshot (keyed "ThumbnailGenerationTask"):
//   - shows only while remaining > 0;
//   - never shows in steady state (nothing to regenerate);
//   - plays a brief "Thumbnails updated" beat at completion, then hides;
//   - stays hidden for the session once dismissed, even if regen continues;
//   - percentage is round(current / total * 100).

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { useTasksStore } from "../../stores/useTasksStore";
import ThumbnailUpgradeBanner from "./ThumbnailUpgradeBanner.vue";

const WORKER_KEY = "ThumbnailGenerationTask";

function snapshot({ total, current, remaining }) {
  return {
    label: "thumbnails_generated",
    current,
    total,
    remaining,
    status: remaining > 0 ? "running" : "idle",
    running: remaining > 0,
    active: remaining > 0,
  };
}

function mountBanner() {
  return mount(ThumbnailUpgradeBanner, {
    global: {
      stubs: { VIcon: { template: "<i><slot /></i>" } },
    },
  });
}

const nf = new Intl.NumberFormat();

let store;

beforeEach(() => {
  setActivePinia(createPinia());
  store = useTasksStore();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ThumbnailUpgradeBanner", () => {
  it("shows while the thumbnail worker is active (remaining > 0)", async () => {
    store.workerSnapshots = {
      [WORKER_KEY]: snapshot({ total: 100, current: 30, remaining: 70 }),
    };
    const wrapper = mountBanner();
    await wrapper.vm.$nextTick();

    const banner = wrapper.find(".tub-banner");
    expect(banner.exists()).toBe(true);
    expect(wrapper.find(".tub-label").text()).toBe("Upgrading thumbnails");
    expect(wrapper.find(".tub-percent").text()).toBe("30%");
    expect(wrapper.find(".tub-counts").text()).toBe(
      `${nf.format(30)} / ${nf.format(100)}`,
    );
    // progressbar exposes the determinate value for screen readers.
    const bar = wrapper.find('[role="progressbar"]');
    expect(bar.attributes("aria-valuenow")).toBe("30");
    expect(bar.attributes("aria-valuemin")).toBe("0");
    expect(bar.attributes("aria-valuemax")).toBe("100");
  });

  it("never shows in steady state (no thumbnail worker / nothing to regenerate)", async () => {
    // No snapshot at all.
    let wrapper = mountBanner();
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".tub-banner").exists()).toBe(false);

    // Snapshot present but already complete from a cold start — no prior active
    // state, so no completion beat and nothing to show.
    store.workerSnapshots = {
      [WORKER_KEY]: snapshot({ total: 100, current: 100, remaining: 0 }),
    };
    wrapper = mountBanner();
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".tub-banner").exists()).toBe(false);
  });

  it("plays a success beat at completion, then hides", async () => {
    vi.useFakeTimers();
    store.workerSnapshots = {
      [WORKER_KEY]: snapshot({ total: 100, current: 90, remaining: 10 }),
    };
    const wrapper = mountBanner();
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".tub-banner").exists()).toBe(true);

    // Regeneration finishes.
    store.workerSnapshots = {
      [WORKER_KEY]: snapshot({ total: 100, current: 100, remaining: 0 }),
    };
    await wrapper.vm.$nextTick();

    // Success beat: still visible, but done state (100% + "updated").
    expect(wrapper.find(".tub-banner").exists()).toBe(true);
    expect(wrapper.find(".tub-label").text()).toBe("Thumbnails updated");
    expect(wrapper.find(".tub-percent").text()).toBe("100%");
    // No "View progress" link once there is nothing left to view.
    expect(wrapper.find(".tub-view").exists()).toBe(false);

    // After the beat, the banner is gone.
    vi.advanceTimersByTime(3000);
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".tub-banner").exists()).toBe(false);
  });

  it("stays hidden for the session once dismissed, even while regen continues", async () => {
    store.workerSnapshots = {
      [WORKER_KEY]: snapshot({ total: 100, current: 20, remaining: 80 }),
    };
    const wrapper = mountBanner();
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".tub-banner").exists()).toBe(true);

    await wrapper.find(".tub-dismiss").trigger("click");
    expect(wrapper.find(".tub-banner").exists()).toBe(false);

    // Regeneration keeps going — the banner must not reappear.
    store.workerSnapshots = {
      [WORKER_KEY]: snapshot({ total: 100, current: 60, remaining: 40 }),
    };
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".tub-banner").exists()).toBe(false);
  });

  it("computes percentage as round(current / total * 100)", async () => {
    store.workerSnapshots = {
      [WORKER_KEY]: snapshot({ total: 32848, current: 3000, remaining: 29848 }),
    };
    const wrapper = mountBanner();
    await wrapper.vm.$nextTick();

    // 3000 / 32848 = 9.13% -> 9%
    expect(wrapper.find(".tub-percent").text()).toBe("9%");
    expect(wrapper.find(".tub-counts").text()).toBe(
      `${nf.format(3000)} / ${nf.format(32848)}`,
    );
    expect(wrapper.find('[role="progressbar"]').attributes("aria-valuenow")).toBe(
      "9",
    );
  });

  it("emits view-progress when the Tasks-tab link is clicked", async () => {
    store.workerSnapshots = {
      [WORKER_KEY]: snapshot({ total: 100, current: 40, remaining: 60 }),
    };
    const wrapper = mountBanner();
    await wrapper.vm.$nextTick();

    await wrapper.find(".tub-view").trigger("click");
    expect(wrapper.emitted("view-progress")).toHaveLength(1);
  });
});
