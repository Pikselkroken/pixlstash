import { describe, it, expect, vi } from "vitest";
import { useSubmitGuard } from "./useSubmitGuard";

/** A handler that resolves only when the test says so. */
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useSubmitGuard", () => {
  it("starts idle", () => {
    const { pending } = useSubmitGuard(() => {});
    expect(pending.value).toBe(false);
  });

  it("is pending while the handler is in flight and idle again after", async () => {
    const gate = deferred();
    const { pending, run } = useSubmitGuard(() => gate.promise);

    const inFlight = run();
    expect(pending.value).toBe(true);

    gate.resolve("done");
    await expect(inFlight).resolves.toBe("done");
    expect(pending.value).toBe(false);
  });

  // The bug itself (#647): two clicks, one create.
  it("ignores a second call while the first is still running", async () => {
    const gate = deferred();
    const handler = vi.fn(() => gate.promise);
    const { run } = useSubmitGuard(handler);

    const first = run();
    const second = run();
    const third = run();

    expect(handler).toHaveBeenCalledTimes(1);
    await expect(second).resolves.toBeUndefined();
    await expect(third).resolves.toBeUndefined();

    gate.resolve();
    await first;
  });

  it("lets a later call through once the first has settled", async () => {
    const handler = vi.fn(async () => "ok");
    const { run } = useSubmitGuard(handler);

    await run();
    await run();

    expect(handler).toHaveBeenCalledTimes(2);
  });

  // The other half of #647: a failed submit must re-enable its button.
  it("clears pending and rethrows when the handler rejects", async () => {
    const boom = new Error("500");
    const { pending, run } = useSubmitGuard(async () => {
      throw boom;
    });

    await expect(run()).rejects.toBe(boom);
    expect(pending.value).toBe(false);
  });

  it("clears pending and rethrows when the handler throws synchronously", async () => {
    const boom = new Error("bad input");
    const { pending, run } = useSubmitGuard(() => {
      throw boom;
    });

    await expect(run()).rejects.toBe(boom);
    expect(pending.value).toBe(false);
  });

  it("allows a retry after a failure", async () => {
    let attempt = 0;
    const handler = vi.fn(async () => {
      attempt += 1;
      if (attempt === 1) throw new Error("transient");
      return "second time lucky";
    });
    const { run } = useSubmitGuard(handler);

    await expect(run()).rejects.toThrow("transient");
    await expect(run()).resolves.toBe("second time lucky");
    expect(handler).toHaveBeenCalledTimes(2);
  });

  // Templates call `run` in place of the handler, so arguments and the return
  // value have to pass straight through - including the click event a bare
  // `@click="run"` hands it.
  it("forwards arguments and the resolved value", async () => {
    const handler = vi.fn(async (a, b) => `${a}:${b}`);
    const { run } = useSubmitGuard(handler);

    await expect(run("set", 7)).resolves.toBe("set:7");
    expect(handler).toHaveBeenCalledWith("set", 7);
  });

  it("wraps a synchronous handler too", async () => {
    const { pending, run } = useSubmitGuard(() => 42);
    await expect(run()).resolves.toBe(42);
    expect(pending.value).toBe(false);
  });

  // Each form owns its own guard; one dialog submitting must not disable another.
  it("keeps separate guards independent", async () => {
    const gate = deferred();
    const a = useSubmitGuard(() => gate.promise);
    const b = useSubmitGuard(async () => "b");

    const inFlight = a.run();
    expect(a.pending.value).toBe(true);
    expect(b.pending.value).toBe(false);

    await expect(b.run()).resolves.toBe("b");

    gate.resolve();
    await inFlight;
  });
});
