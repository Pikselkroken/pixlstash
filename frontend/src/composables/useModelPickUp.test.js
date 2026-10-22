import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../api/modelFolders", () => ({ pickUpModelFiles: vi.fn() }));

import { pickUpModelFiles } from "../api/modelFolders";
import { LOOK_AGAIN_MS, useModelPickUp } from "./useModelPickUp";

beforeEach(() => {
  vi.useRealTimers();
  pickUpModelFiles.mockReset();
});

describe("useModelPickUp", () => {
  it("only looks when nothing is found, and says nothing is being added", async () => {
    pickUpModelFiles.mockResolvedValue([]);
    const { pickUp, adding } = useModelPickUp();

    expect(await pickUp(["sdxl/gone.safetensors"])).toBe(false);

    expect(pickUpModelFiles).toHaveBeenCalledTimes(1);
    expect(pickUpModelFiles).toHaveBeenCalledWith(["sdxl/gone.safetensors"]);
    expect(adding.value.size).toBe(0);
  });

  it("registers what it found and marks those files while it does", async () => {
    let finish;
    pickUpModelFiles
      .mockResolvedValueOnce([{ folder_id: 1, relpath: "sub/Late.safetensors" }])
      .mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    const { pickUp, isAdding } = useModelPickUp();

    const done = pickUp(["sdxl\\late.safetensors", "other.safetensors"]);
    await vi.waitFor(() => expect(pickUpModelFiles).toHaveBeenCalledTimes(2));

    expect(pickUpModelFiles).toHaveBeenLastCalledWith(
      ["sdxl\\late.safetensors", "other.safetensors"],
      { add: true },
    );
    expect(isAdding("sdxl\\late.safetensors")).toBe(true);
    expect(isAdding("other.safetensors")).toBe(false);

    finish([{ folder_id: 1, relpath: "sub/Late.safetensors", model_id: 7 }]);
    expect(await done).toBe(true);
    expect(isAdding("sdxl\\late.safetensors")).toBe(false);
  });

  it("does not look for the same name again within the window", async () => {
    vi.useFakeTimers();
    pickUpModelFiles.mockResolvedValue([]);
    const { pickUp } = useModelPickUp();

    await pickUp(["a.safetensors"]);
    await pickUp(["a.safetensors"]);
    expect(pickUpModelFiles).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(LOOK_AGAIN_MS);
    await pickUp(["a.safetensors"]);
    expect(pickUpModelFiles).toHaveBeenCalledTimes(2);
  });

  it("leaves the file missing when the server refuses", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    pickUpModelFiles.mockRejectedValue(new Error("403"));
    const { pickUp, adding } = useModelPickUp();

    expect(await pickUp(["a.safetensors"])).toBe(false);
    expect(adding.value.size).toBe(0);
  });
});
