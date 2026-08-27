import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  useConfirm,
  registerConfirmHost,
  unregisterConfirmHost,
  activeConfirm,
  resolveConfirm,
} from "./useConfirm";

// Module-level singleton state - reset the host registration between tests so
// the native-fallback and host paths don't leak into each other.
beforeEach(() => {
  unregisterConfirmHost();
  vi.stubGlobal("confirm", vi.fn());
});

afterEach(() => {
  unregisterConfirmHost();
  vi.unstubAllGlobals();
});

describe("useConfirm - native fallback (no host mounted)", () => {
  it("resolves true when window.confirm accepts", async () => {
    window.confirm.mockReturnValue(true);
    const { confirm } = useConfirm();
    await expect(confirm("Delete it?")).resolves.toBe(true);
    expect(window.confirm).toHaveBeenCalledWith("Delete it?");
  });

  it("resolves false when window.confirm cancels", async () => {
    window.confirm.mockReturnValue(false);
    const { confirm } = useConfirm();
    await expect(confirm({ message: "Sure?" })).resolves.toBe(false);
  });
});

describe("useConfirm - host mounted", () => {
  it("does NOT call window.confirm and waits for the host to resolve", async () => {
    registerConfirmHost();
    const { confirm } = useConfirm();
    const pending = confirm({ title: "T", message: "M", danger: true });

    // The request is exposed for the host to render, native confirm untouched.
    expect(window.confirm).not.toHaveBeenCalled();
    expect(activeConfirm.value).toMatchObject({
      options: { title: "T", message: "M", danger: true },
    });

    resolveConfirm(true);
    await expect(pending).resolves.toBe(true);
    expect(activeConfirm.value).toBeNull();
  });

  it("normalizes a bare string into the message", async () => {
    registerConfirmHost();
    const { confirm } = useConfirm();
    const pending = confirm("just a message");
    expect(activeConfirm.value.options.message).toBe("just a message");
    resolveConfirm(false);
    await expect(pending).resolves.toBe(false);
  });

  it("unregistering the host cancels a dangling request", async () => {
    registerConfirmHost();
    const { confirm } = useConfirm();
    const pending = confirm("hang?");
    unregisterConfirmHost();
    await expect(pending).resolves.toBe(false);
  });
});
