// The import receipt's countdown: a landed receipt leaves by itself, pauses
// while it is being read, and stays once it was used or reports files the
// server could not read.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";

import ArrivingTiles from "./ArrivingTiles.vue";
import { useImportArrivalsStore } from "../../stores/useImportArrivalsStore";

const DWELL = 8000;
const BUTTON = { template: "<button type=\"button\"><slot /></button>" };

let wrapper = null;

beforeEach(() => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
  URL.createObjectURL = vi.fn(() => null);
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.useRealTimers();
});

function landed(store, id, outcome = "duplicate") {
  const keys = store.begin(id, [new File(["x"], `${id}.png`)]);
  store.markChecking(id);
  store.applyOutcomes(id, [{ key: keys[0], outcome, picture_id: 7 }]);
  store.land(id);
}

async function mountTiles() {
  wrapper = mount(ArrivingTiles, {
    global: { stubs: { VIcon: true, AppBarButton: BUTTON } },
  });
  await nextTick();
  return wrapper;
}

const ids = (store) => store.runs.map((run) => run.id);

describe("ArrivingTiles receipt countdown", () => {
  it("drains and dismisses a landed receipt", async () => {
    const store = useImportArrivalsStore();
    landed(store, "r1", "new");
    await mountTiles();

    expect(wrapper.find('[data-testid="arriving-countdown"]').exists()).toBe(
      true,
    );
    vi.advanceTimersByTime(DWELL - 1);
    expect(ids(store)).toEqual(["r1"]);
    vi.advanceTimersByTime(1);
    expect(ids(store)).toEqual([]);
  });

  it("never counts down while the import is still running", async () => {
    const store = useImportArrivalsStore();
    store.begin("r1", [new File(["x"], "a.png")]);
    await mountTiles();

    expect(wrapper.find('[data-testid="arriving-countdown"]').exists()).toBe(
      false,
    );
    vi.advanceTimersByTime(DWELL * 3);
    expect(ids(store)).toEqual(["r1"]);
  });

  it("pauses while hovered and resumes from where it stopped", async () => {
    const store = useImportArrivalsStore();
    landed(store, "r1", "new");
    await mountTiles();
    const section = wrapper.find('[data-arriving-run="r1"]');

    vi.advanceTimersByTime(DWELL - 1000);
    await section.trigger("mouseenter");
    expect(
      wrapper.find(".arr-countdown").classes("arr-countdown--paused"),
    ).toBe(true);
    vi.advanceTimersByTime(DWELL * 3);
    expect(ids(store)).toEqual(["r1"]);

    await section.trigger("mouseleave");
    vi.advanceTimersByTime(999);
    expect(ids(store)).toEqual(["r1"]);
    vi.advanceTimersByTime(1);
    expect(ids(store)).toEqual([]);
  });

  it("restarts the bar where the timer stopped", async () => {
    const store = useImportArrivalsStore();
    landed(store, "r1", "new");
    await mountTiles();
    const bar = () => wrapper.find(".arr-countdown i").attributes("style");
    expect(bar()).toContain("animation-delay: 0ms");

    vi.advanceTimersByTime(3000);
    await wrapper.find('[data-arriving-run="r1"]').trigger("mouseenter");
    expect(bar()).toContain("animation-delay: -3000ms");
  });

  it("pauses while focus is inside, not while it moves within", async () => {
    const store = useImportArrivalsStore();
    landed(store, "r1", "new");
    await mountTiles();
    const section = wrapper.find('[data-arriving-run="r1"]');
    const inner = section.find(".arr-band").element;

    await section.trigger("focusin");
    await section.trigger("focusout", { relatedTarget: inner });
    vi.advanceTimersByTime(DWELL * 3);
    expect(ids(store)).toEqual(["r1"]);

    await section.trigger("focusout", { relatedTarget: null });
    vi.advanceTimersByTime(DWELL);
    expect(ids(store)).toEqual([]);
  });

  it("pauses while the tab is hidden", async () => {
    const store = useImportArrivalsStore();
    landed(store, "r1", "new");
    await mountTiles();
    const hidden = vi.spyOn(document, "hidden", "get");

    hidden.mockReturnValue(true);
    document.dispatchEvent(new Event("visibilitychange"));
    vi.advanceTimersByTime(DWELL * 3);
    expect(ids(store)).toEqual(["r1"]);

    hidden.mockReturnValue(false);
    document.dispatchEvent(new Event("visibilitychange"));
    vi.advanceTimersByTime(DWELL);
    expect(ids(store)).toEqual([]);
    hidden.mockRestore();
  });

  it("stays once its hidden tiles were shown", async () => {
    const store = useImportArrivalsStore();
    const files = Array.from(
      { length: 4 },
      (_, i) => new File(["x"], `p${i}.png`),
    );
    store.begin("r1", files);
    store.land("r1");
    wrapper = mount(ArrivingTiles, {
      props: { columns: 1 },
      global: { stubs: { VIcon: true, AppBarButton: BUTTON } },
    });
    await nextTick();

    await wrapper.find("button.arr-more").trigger("click");
    vi.advanceTimersByTime(DWELL * 3);
    expect(ids(store)).toEqual(["r1"]);
  });

  it("stays once a chip was used, even toggled back off", async () => {
    const store = useImportArrivalsStore();
    landed(store, "r1", "duplicate");
    await mountTiles();

    const chip = wrapper.find("button.arr-chip");
    await chip.trigger("click");
    await chip.trigger("click");
    expect(wrapper.find('[data-testid="arriving-countdown"]').exists()).toBe(
      false,
    );
    vi.advanceTimersByTime(DWELL * 3);
    expect(ids(store)).toEqual(["r1"]);
  });

  it("stays when the server could not read a file", async () => {
    const store = useImportArrivalsStore();
    landed(store, "r1", "failed");
    await mountTiles();

    vi.advanceTimersByTime(DWELL * 3);
    expect(ids(store)).toEqual(["r1"]);
  });
});
