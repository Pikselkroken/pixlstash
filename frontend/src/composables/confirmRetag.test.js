import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { confirmRetag } from "./confirmRetag";
import {
  activeConfirm,
  registerConfirmHost,
  resolveConfirm,
  unregisterConfirmHost,
} from "./useConfirm";

beforeEach(() => {
  window.localStorage.clear();
  registerConfirmHost();
});
afterEach(() => unregisterConfirmHost());

describe("confirmRetag", () => {
  it("warns that hand-added tags go too, pluralised for a selection", async () => {
    const one = confirmRetag(1);
    expect(activeConfirm.value.options.title).toBe("Retag this picture?");
    expect(activeConfirm.value.options.message).toContain("added by hand");
    resolveConfirm(false);
    await expect(one).resolves.toBe(false);

    const many = confirmRetag(12);
    expect(activeConfirm.value.options.title).toBe("Retag 12 pictures?");
    expect(activeConfirm.value.options.danger).toBe(true);
    resolveConfirm(true);
    await expect(many).resolves.toBe(true);
  });

  it("stops asking once confirmed with don't-show-again", async () => {
    const first = confirmRetag(3);
    resolveConfirm(true, true);
    await expect(first).resolves.toBe(true);

    await expect(confirmRetag(3)).resolves.toBe(true);
    expect(activeConfirm.value).toBeNull();
  });
});
