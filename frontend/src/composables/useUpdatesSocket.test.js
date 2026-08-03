import { describe, expect, it, vi } from "vitest";

import { handleUpdatesSocketClose } from "./useUpdatesSocket";

describe("updates socket close lifecycle", () => {
  it("reloads a non-initiating client after a library switch", () => {
    const reload = vi.fn();
    const reconnect = vi.fn();

    handleUpdatesSocketClose(
      { code: 1012, reason: "Library switched" },
      { reload, reconnect },
    );

    expect(reload).toHaveBeenCalledOnce();
    expect(reconnect).not.toHaveBeenCalled();
  });

  it("reconnects after an ordinary transport close", () => {
    const reload = vi.fn();
    const reconnect = vi.fn();

    handleUpdatesSocketClose(
      { code: 1006, reason: "" },
      { reload, reconnect },
    );

    expect(reload).not.toHaveBeenCalled();
    expect(reconnect).toHaveBeenCalledOnce();
  });
});
