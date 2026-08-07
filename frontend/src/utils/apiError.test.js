import { describe, it, expect } from "vitest";
import { errorDetail, errorMessage } from "./apiError";

const err = (detail, status) => ({ response: { data: { detail }, status } });

describe("errorDetail", () => {
  it("returns the server's sentence, trimmed", () => {
    expect(errorDetail(err("  A stack needs two pictures.  "))).toBe(
      "A stack needs two pictures.",
    );
  });

  it("names the sets on a structured locked-set refusal", () => {
    const detail = { code: "locked_set", sets: [{ name: "Keepers" }] };
    expect(errorDetail(err(detail, 423))).toContain("Keepers");
  });

  // The bug this module exists for: `detail || fallback` takes the OBJECT when
  // the refusal is structured, and the user reads "[object Object]".
  it("never returns a non-string", () => {
    for (const detail of [{ code: "something_else" }, [{ msg: "x" }], 42, null])
      expect(typeof errorDetail(err(detail))).toBe("string");
  });

  it("is empty when there is no response at all", () => {
    expect(errorDetail(new Error("Network Error"))).toBe("");
    expect(errorDetail(undefined)).toBe("");
  });
});

describe("errorMessage", () => {
  it("prefers the server, then the transport, then the caller's copy", () => {
    expect(errorMessage(err("Refused."), "fallback")).toBe("Refused.");
    expect(errorMessage(new Error("Network Error"), "fallback")).toBe(
      "Network Error",
    );
    expect(errorMessage({}, "Could not save.")).toBe("Could not save.");
  });
});
