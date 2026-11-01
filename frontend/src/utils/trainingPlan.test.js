// The Train a LoRA dialog's read-outs and prompt edits (#2119).

import { describe, it, expect } from "vitest";
import {
  TRIGGER_TOKEN,
  effortHint,
  effortReadout,
  fitReadout,
  moreRows,
  timeEstimate,
  parseTyped,
  promptFromDisplay,
  promptLines,
  promptsEdited,
  runName,
  sourceLabel,
  toggleTrigger,
  triggerFromName,
  pronounOf,
  promptRole,
  roleText,
} from "./trainingPlan";

const STANDARD = {
  steps_per_picture: 100,
  min_steps: 1200,
  max_steps: 3000,
  fits: { lo: 12, hi: 30 },
};

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
        clamp: null,
        effort: "standard",
        pictureCount: 24,
      }),
    ).toBe("2,400 steps (about 100 per picture) · rank 32 · LR 0.0001");
  });

  it("names the cap of a big set and the floor of a small one", () => {
    expect(
      effortReadout({
        values: values(3000),
        clamp: "cap",
        effort: "standard",
        pictureCount: 112,
      }),
    ).toBe("3,000 steps (Standard's cap; 112 pictures) · rank 32 · LR 0.0001");
    expect(
      effortReadout({
        values: values(1200),
        clamp: "floor",
        effort: "standard",
        pictureCount: 8,
      }),
    ).toMatch(/^1,200 steps \(Standard's minimum; 8 pictures\)/);
  });

  it("says a typed step count is pinned", () => {
    expect(
      effortReadout({
        values: values(2000, "pinned"),
        clamp: null,
        effort: "standard",
        pictureCount: 24,
      }),
    ).toMatch(/^2,000 steps \(pinned\)/);
  });

  it("says the picture range the server gives, and the cap when held", () => {
    expect(
      effortHint({ rule: STANDARD, clamp: null, effort: "standard", subject: "person" }),
    ).toBe("Standard suits 12 to 30 pictures.");
    expect(
      effortHint({ rule: STANDARD, clamp: "cap", effort: "standard", subject: "person" }),
    ).toBe(
      "Standard suits 12 to 30 pictures and stops at 3,000 steps for bigger sets.",
    );
    expect(
      effortHint({ rule: STANDARD, clamp: null, effort: "strong", subject: "person" }),
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
    clamp: null,
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
    expect(sourceLabel({ source: "pictures" }, { ...ctx, clamp: "cap" })).toBe(
      "Standard, capped",
    );
  });

  it("reads the memory profile with its peak", () => {
    expect(
      fitReadout({ profile_label: "8-bit model · text encoder on the GPU", vram_mb: 16000 }),
    ).toBe("8-bit model · text encoder on the GPU · peaks near 16 GB");
  });

  it("says a measured peak is measured and when previews wait", () => {
    expect(
      fitReadout({
        profile_label: "8-bit model",
        vram_mb: 12288,
        evidence: "measured",
        previews_deferred: true,
      }),
    ).toBe("8-bit model · peaks at 12 GB, measured · previews after training");
  });

  it("gives the time only from a measurement", () => {
    expect(timeEstimate({ estimate: { seconds: 3000, basis: "measured" } })).toBe(
      "About 50 min.",
    );
    expect(timeEstimate({ estimate: { seconds: 5400, basis: "measured" } })).toBe(
      "About 1 h 30 min.",
    );
    expect(timeEstimate({ estimate: { seconds: null, basis: "none" }, probe: true })).toBe(
      "It checks memory and speed first, so the time is not known yet.",
    );
    expect(timeEstimate({ estimate: { seconds: null, basis: "none" } })).toBe("");
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

describe("runName", () => {
  it("names the subject and the model, numbered after the runs named so", () => {
    const names = [
      "Grandad Z-Image Turbo #1",
      "Grandad Z-Image Turbo #3",
      "Grandad Krea 2 Raw #7",
      "Grandad Z-Image Turbo #2b",
      "Grandad (grandad)",
      null,
    ];
    expect(runName("Grandad", "Z-Image Turbo", names)).toBe(
      "Grandad Z-Image Turbo #4",
    );
    expect(runName("Grandad", "Krea 2 Raw", names)).toBe("Grandad Krea 2 Raw #8");
    expect(runName("Pub nights", "Z-Image Turbo", names)).toBe(
      "Pub nights Z-Image Turbo #1",
    );
  });

  it("starts at one, and names a LoRA when nothing else is known", () => {
    expect(runName("", "", [])).toBe("LoRA #1");
    expect(runName("Grandad", undefined, undefined)).toBe("Grandad #1");
    expect(runName("  Grandad ", " Z-Image Turbo", [])).toBe(
      "Grandad Z-Image Turbo #1",
    );
    const long = runName("x".repeat(300), "Z-Image Turbo", []);
    expect(long.length).toBeLessThanOrEqual(200);
    expect(long).toMatch(/^x+ Z-Image Turbo #1$/);
  });
});

describe("the control prompts' roles", () => {
  const info = { description: "an elderly man", stranger: "a young woman" };

  it("names a person by their description's noun", () => {
    expect(pronounOf("an elderly man")).toBe("him");
    expect(pronounOf("a young woman")).toBe("her");
    expect(pronounOf("a person")).toBe("them");
    expect(pronounOf(undefined)).toBe("them");
  });

  it("tells the lookalike from the stranger, and claims nothing else", () => {
    expect(promptRole("an elderly man reading", info)).toBe("lookalike");
    expect(promptRole("A young woman at a cafe", info)).toBe("stranger");
    expect(promptRole("{{TRIGGER_WORD}} reading", info)).toBeNull();
    expect(promptRole("a dog in a park", info)).toBeNull();
    expect(promptRole("a mango", { description: "a man", stranger: "a woman" })).toBeNull();
    // A thing's prompts carry no stranger, so no roles.
    expect(promptRole("a red bicycle", { description: "a red bicycle" })).toBeNull();
  });

  it("words the lookalike by whether the description was kept generic", () => {
    const d = { description: "an elderly man" };
    expect(roleText("lookalike", { ...d, kept: true })).toEqual({
      label: "Like him",
      expect: "Should not change",
      detail: "kept generic",
    });
    expect(roleText("lookalike", { ...d, kept: false }).expect).toBe("Expected to drift");
    expect(roleText("stranger", { ...d, kept: false }).expect).toBe("Should not change");
    expect(roleText(null, d)).toBeNull();
  });
});
