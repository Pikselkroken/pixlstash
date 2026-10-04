// A workflow name read under one credential must not land under the next.

import { describe, it, expect, vi } from "vitest";
import { flushPromises } from "@vue/test-utils";

let release;
vi.mock("../api/workflows", () => ({
  getWorkflowCard: vi.fn(
    () => new Promise((resolve) => (release = () => resolve({ card: { name: "Old name" } }))),
  ),
}));

import { useWorkflowNames } from "./useWorkflowNames";
import { notifySessionReset } from "../utils/apiClient";

describe("useWorkflowNames", () => {
  it("drops a read that lands after a session reset", async () => {
    const { nameOf } = useWorkflowNames();
    expect(nameOf("auto:late")).toBeNull();
    await flushPromises();
    notifySessionReset();
    release();
    await flushPromises();
    expect(nameOf("auto:late")).toBeNull();
  });
});
