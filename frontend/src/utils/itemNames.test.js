import { describe, expect, it } from "vitest";
import { isPlaceholderName, nameFromDescription } from "./itemNames";

describe("an item's name from its description", () => {
  it("takes the first clause, without its article, capitalised", () => {
    expect(nameFromDescription("a mustard raincoat over a white shirt, rubber boots")).toBe(
      "Mustard raincoat over a white shirt",
    );
    expect(nameFromDescription("the red umbrella. Wet.")).toBe("Red umbrella");
    expect(nameFromDescription("   ")).toBe("");
  });

  it("cuts a long one on a word", () => {
    const name = nameFromDescription(
      "an oversized hand-knitted cable jumper in faded forest green wool with leather elbow patches",
    );
    expect(name.length).toBeLessThanOrEqual(40);
    expect(name).toBe("Oversized hand-knitted cable jumper in");
  });

  it("knows a name given for want of one", () => {
    expect(isPlaceholderName("New outfit", "New outfit")).toBe(true);
    expect(isPlaceholderName("New outfit copy 2", "New outfit")).toBe(true);
    expect(isPlaceholderName("New outfit (Juno)", "New outfit")).toBe(true);
    expect(isPlaceholderName("Mustard raincoat", "New outfit")).toBe(false);
  });
});
