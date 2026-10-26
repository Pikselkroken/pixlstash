// The Train a LoRA dialog's read-outs and prompt edits (#2119).

import { describe, it, expect } from "vitest";
import {
  TRIGGER_TOKEN,
  effortFits,
  effortHint,
  effortReadout,
  fitReadout,
  moreRows,
  parseTyped,
  promptFromDisplay,
  promptLines,
  promptsEdited,
  sourceLabel,
  toggleTrigger,
  triggerFromName,
} from "./trainingPlan";

const STANDARD = { steps_per_picture: 100, min_steps: 1200, max_steps: 3000 };

describe("triggerFromName", () => {
  it("lowercases and drops what is not a letter or digit", () => {
    expect(triggerFromName("Grandad")).toBe("grandad");
    expect(triggerFromName("Pub nights")).toBe("pubnights");
    expect(triggerFromName("Zoë's café")).toBe("zoescafe");
  });
});

describe("effort read-out", () => {
  const values = (steps, source = "pictures") => ({
    "train.steps": { value: steps, source },
    "network.linear": { value: 32, source: "effort" },
    "train.lr": { value: 0.0001, source: "effort" },
  });

  it("says what the effort set, per picture", () => {
    expect(
      effortReadout({
        values: values(2400),
        rule: STANDARD,
        effort: "standard",
        pictureCount: 24,
      }),
    ).toBe("2,400 steps (about 100 per picture) · rank 32 · LR 0.0001");
  });

  it("names the cap of a big set and the floor of a small one", () => {
    expect(
      effortReadout({
        values: values(3000),
        rule: STANDARD,
        effort: "standard",
        pictureCount: 112,
      }),
    ).toBe("3,000 steps (Standard's cap; 112 pictures) · rank 32 · LR 0.0001");
    expect(
      effortReadout({
        values: values(1200),
        rule: STANDARD,
        effort: "standard",
        pictureCount: 8,
      }),
    ).toMatch(/^1,200 steps \(Standard's minimum; 8 pictures\)/);
  });

  it("says a typed step count is pinned", () => {
    expect(
      effortReadout({
        values: values(2000, "pinned"),
        rule: STANDARD,
        effort: "standard",
        pictureCount: 24,
      }),
    ).toMatch(/^2,000 steps \(pinned\)/);
  });

  it("derives the picture range from the step rule", () => {
    expect(effortFits(STANDARD)).toEqual({ lo: 12, hi: 30 });
    expect(
      effortHint({ rule: STANDARD, effort: "standard", subject: "person", pictureCount: 24 }),
    ).toBe("Standard suits 12 to 30 pictures.");
    expect(
      effortHint({ rule: STANDARD, effort: "strong", subject: "person", pictureCount: 24 }),
    ).toMatch(/learns the face sooner/);
  });
});

describe("sources", () => {
  const ctx = {
    effort: "standard",
    fit: "auto",
    subject: "style",
    character: null,
    pictureCount: 24,
    rule: STANDARD,
    presetLabel: "Z-Image Turbo",
    room: { vram_mb: 20 * 1024 },
  };

  it("names the choice, the picture count, the GPU or the model", () => {
    expect(sourceLabel({ source: "pictures" }, ctx)).toBe("Standard × 24 pictures");
    expect(sourceLabel({ source: "effort" }, ctx)).toBe("Standard");
    expect(sourceLabel({ source: "subject" }, ctx)).toBe("A style");
    expect(sourceLabel({ source: "subject" }, { ...ctx, character: { id: 1 } })).toBe(
      "Character LoRA",
    );
    expect(sourceLabel({ source: "gpu" }, ctx)).toBe("Auto, 20 GB free");
    expect(sourceLabel({ source: "model" }, ctx)).toBe("Z-Image Turbo");
    expect(sourceLabel({ source: "pinned" }, ctx)).toBe("Typed by you, so it stays put");
    expect(sourceLabel({ source: "pictures" }, { ...ctx, pictureCount: 112 })).toBe(
      "Standard, capped",
    );
  });

  it("reads the memory profile with its peak", () => {
    expect(
      fitReadout({ profile_label: "8-bit model · text encoder on the GPU", vram_mb: 16000 }),
    ).toBe("8-bit model · text encoder on the GPU · peaks near 16 GB");
  });

  it("keeps every other resolved key for Show more, never a first-row key", () => {
    const rows = moreRows({
      "train.steps": {},
      "model.qtype": {},
      "sample.sample_steps": {},
    });
    expect(rows.map((r) => r.keys[0])).toEqual(["model.qtype", "sample.sample_steps"]);
  });
});

describe("typed values", () => {
  it("checks the rule and leaves a blank field to the choice", () => {
    expect(parseTyped({ kind: "int", lo: 1, hi: 100000 }, "2400")).toEqual({ value: 2400 });
    expect(parseTyped({ kind: "int", lo: 1, hi: 100000 }, "2.5").error).toBeTruthy();
    expect(parseTyped({ kind: "int", lo: 1, hi: 100000 }, "0").error).toBeTruthy();
    expect(parseTyped({ kind: "float", lo: 1e-7, hi: 0.1 }, "1e-4")).toEqual({
      value: 0.0001,
    });
    expect(parseTyped({ kind: "float", lo: 1e-7, hi: 0.1 }, " ")).toEqual({ value: null });
  });
});

describe("sample prompts", () => {
  const written = [
    {
      prompt: `photo of ${TRIGGER_TOKEN} smiling`,
      with_trigger: `photo of ${TRIGGER_TOKEN} smiling`,
      without_trigger: "photo of an old man smiling",
    },
    {
      prompt: "photo of an old man smiling",
      with_trigger: `photo of ${TRIGGER_TOKEN} smiling`,
      without_trigger: "photo of an old man smiling",
    },
  ];

  it("swaps a written line between its two forms", () => {
    const [first, second] = promptLines(written);
    expect(toggleTrigger(first).text).toBe("photo of an old man smiling");
    expect(toggleTrigger(second).text).toBe(`photo of ${TRIGGER_TOKEN} smiling`);
    expect(toggleTrigger(toggleTrigger(first))).toEqual(first);
  });

  it("adds or takes out the word on a line the owner wrote", () => {
    expect(toggleTrigger({ text: "a harbour at dusk", alt: null }).text).toBe(
      `${TRIGGER_TOKEN}, a harbour at dusk`,
    );
    expect(toggleTrigger({ text: `${TRIGGER_TOKEN}, a harbour at dusk`, alt: null }).text).toBe(
      "a harbour at dusk",
    );
  });

  it("turns the typed trigger word back into the placeholder, whole words only", () => {
    expect(promptFromDisplay("Grandad by the sea, grandads", "grandad")).toBe(
      `${TRIGGER_TOKEN} by the sea, grandads`,
    );
  });

  it("is a pin once the lines differ from the written ones", () => {
    const lines = promptLines(written);
    expect(promptsEdited(lines, lines)).toBe(false);
    expect(promptsEdited(lines.slice(1), lines)).toBe(true);
    expect(promptsEdited([...lines, { text: "  ", alt: null }], lines)).toBe(false);
  });
});
