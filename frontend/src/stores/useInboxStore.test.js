import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    onSessionReset: () => () => {},
    apiClient: { get: vi.fn() },
    isReadOnly: ref(false),
  };
});

import { inboxEmptyState, useInboxStore } from "./useInboxStore";
import { useProjectStore } from "./useProjectStore";

beforeEach(() => {
  window.localStorage.removeItem("pixlstash:inboxPeriod");
  setActivePinia(createPinia());
});

describe("useInboxStore", () => {
  it("starts on the last 2 weeks, and ignores a period it does not know", () => {
    expect(useInboxStore().period).toBe("14d");
    window.localStorage.setItem("pixlstash:inboxPeriod", "2w");
    setActivePinia(createPinia());
    const store = useInboxStore();
    expect(store.period).toBe("14d");
    store.setPeriod("fortnight");
    expect(store.period).toBe("14d");
  });

  it("counts inside the sidebar's project, as the grid it opens does", () => {
    const store = useInboxStore();
    const query = (id) => Object.fromEntries(new URLSearchParams(store.countQuery(id)));
    expect(query("30d")).toEqual({ inbox: "30d", stack_leaders_only: "true" });
    const projects = useProjectStore();
    projects.projectViewMode = "project";
    projects.selectedProjectId = 7;
    expect(query("30d").project_id).toBe("7");
    projects.selectedProjectId = null;
    expect(query("30d").project_id).toBe("UNASSIGNED");
  });
});

describe("useInboxStore counts", () => {
  it("drops the previous project's counts when the scope changes", async () => {
    const { apiClient } = await import("../utils/apiClient");
    apiClient.get.mockImplementation(async (url) => ({
      data: { count: String(url).includes("project_id=7") ? 5 : 40 },
    }));
    const store = useInboxStore();
    await store.refreshAllCounts();
    expect(store.counts["90d"]).toBe(40);
    const projects = useProjectStore();
    projects.projectViewMode = "project";
    projects.selectedProjectId = 7;
    await store.refreshCount();
    expect(store.counts).toEqual({ "14d": 5 });
  });
});

describe("useInboxStore refreshSoon", () => {
  it("coalesces a burst into one read of the counts on show", async () => {
    vi.useFakeTimers();
    try {
      const { apiClient } = await import("../utils/apiClient");
      apiClient.get.mockReset();
      apiClient.get.mockResolvedValue({ data: { count: 3 } });
      const store = useInboxStore();
      store.counts = { "14d": 1, "90d": 2 };
      for (let i = 0; i < 5; i += 1) store.refreshSoon();
      expect(apiClient.get).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1500);
      const periods = apiClient.get.mock.calls.map(([url]) =>
        new URLSearchParams(String(url).split("?")[1]).get("inbox"),
      );
      expect(periods.sort()).toEqual(["14d", "90d"]);
      expect(store.counts).toEqual({ "14d": 3, "90d": 3 });
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("inboxEmptyState", () => {
  const now = new Date(2026, 10, 1, 12); // Sun 1 Nov

  it("names its period and the date it reaches back to", () => {
    const { title, subtitle, wider } = inboxEmptyState("14d", {}, now);
    expect(title).toBe("Nothing new in the last 2 weeks");
    expect(subtitle).toMatch(/^Everything added since .*18.* has a person or a set\.$/);
    expect(wider).toBeNull();
  });

  it("offers the narrowest wider period that holds anything", () => {
    const counts = { "14d": 0, "30d": 0, "90d": 347, all: 900 };
    const { subtitle, wider } = inboxEmptyState("14d", counts, now);
    expect(wider.id).toBe("90d");
    expect(subtitle).toContain(
      "347 pictures from the three months before still have neither.",
    );
    expect(inboxEmptyState("90d", counts, now).subtitle).toContain(
      "900 pictures added before then still have neither.",
    );
    expect(inboxEmptyState("24h", { "7d": 1 }, now).subtitle).toContain(
      "1 picture from the week before still has neither.",
    );
  });

  it("has nowhere wider to go at any time", () => {
    expect(inboxEmptyState("all", { all: 0 }, now)).toEqual({
      title: "Nothing to file",
      subtitle: "Every picture has a person or a set.",
      wider: null,
    });
  });
});
