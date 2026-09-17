// The shared count cache: a response that lands after a reset must not fill
// the new cache, a failure must be asked again, and no more than six requests
// may be in flight at once.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { flushPromises } from "@vue/test-utils";

import { getPictureCount } from "../api/pictures";
import { resetFilterCounts, useFilterCounts } from "./useFilterCounts";

vi.mock("../api/pictures", () => ({ getPictureCount: vi.fn() }));

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  getPictureCount.mockReset();
  resetFilterCounts();
});

describe("useFilterCounts", () => {
  it("discards a response that lands after a reset", async () => {
    const first = deferred();
    const second = deferred();
    getPictureCount
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    let base = "set_id=1";
    const { count } = useFilterCounts(() => base);

    count("");
    await flushPromises();
    resetFilterCounts();
    base = "set_id=2";
    count("");
    await flushPromises();

    second.resolve({ count: 20 });
    await flushPromises();
    first.resolve({ count: 10 });
    await flushPromises();

    expect(count("")).toBe(20);
    getPictureCount.mockResolvedValueOnce({ count: 10 });
    base = "set_id=1";
    expect(count("")).toBeUndefined();
    await flushPromises();
  });

  it("asks again after a failure instead of staying blank", async () => {
    getPictureCount
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ count: 7 });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { count } = useFilterCounts(() => "set_id=1");

    count("face_filter=with_face");
    await flushPromises();
    expect(count("face_filter=with_face")).toBeUndefined();
    await flushPromises();
    expect(count("face_filter=with_face")).toBe(7);
    expect(getPictureCount).toHaveBeenCalledTimes(2);
    warn.mockRestore();
  });

  it("keeps at most six requests in flight", async () => {
    const pending = [];
    getPictureCount.mockImplementation(() => {
      const d = deferred();
      pending.push(d);
      return d.promise;
    });
    const { count } = useFilterCounts(() => "set_id=1");
    for (let i = 0; i < 10; i++) count(`comfyui_model=m${i}`);
    await flushPromises();
    expect(getPictureCount).toHaveBeenCalledTimes(6);

    pending[0].resolve({ count: 1 });
    await flushPromises();
    expect(getPictureCount).toHaveBeenCalledTimes(7);
    // Settle the rest so the next test starts with every slot free.
    for (const d of pending) d.resolve({ count: 1 });
    await flushPromises();
    for (const d of pending) d.resolve({ count: 1 });
    await flushPromises();
  });

  it("counts only the total under a likeness sort", async () => {
    getPictureCount.mockResolvedValue({ count: 5 });
    const { count } = useFilterCounts(
      () => "sort=CHARACTER_LIKENESS&reference_character_id=3",
    );
    expect(count("face_filter=with_face")).toBeUndefined();
    count("");
    await flushPromises();
    expect(getPictureCount).toHaveBeenCalledTimes(1);
  });

  it("asks nothing when the view cannot be counted", async () => {
    const { count } = useFilterCounts(() => null);
    expect(count("")).toBeUndefined();
    await flushPromises();
    expect(getPictureCount).not.toHaveBeenCalled();
  });
});
