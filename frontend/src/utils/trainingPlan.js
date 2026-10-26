// Pure helpers for the Train a LoRA dialog (#2119): how a resolved run
// (POST /training/resolve) reads on screen, and the sample-prompt edits.
// No reactive state here; the dialog and useTrainingStore hold it.

/** The placeholder the server fills with the trigger word at render. */
export const TRIGGER_TOKEN = "{{TRIGGER_WORD}}";

export const PROMPTS_KEY = "sample.samples[*].prompt";

export const SUBJECT_LABELS = {
  person: "A person",
  style: "A style",
  thing: "A thing",
};

export const EFFORT_LABELS = {
  gentle: "Gentle",
  standard: "Standard",
  strong: "Strong",
};

export const FIT_LABELS = {
  auto: "Auto",
  save_memory: "Save memory",
  best_quality: "Best quality",
};

/** How each recipe would fit this machine, as the model list says it. */
export const VERDICT_LABELS = {
  fits: "Fits",
  streams: "Fits, streams from RAM (slower)",
  needs_ram: "Needs more RAM",
  no_gpu: "Not supported on this GPU",
  needs_checkout: "Needs a newer ai-toolkit",
};

/**
 * The Advanced table's first rows, in the design's order. A row with two keys
 * edits both; `prompts` is the sample-prompt row, drawn by the dialog.
 */
export const PRIMARY_ROWS = [
  { label: "Steps", keys: ["train.steps"] },
  { label: "Learning rate", keys: ["train.lr"] },
  { label: "LoRA rank / alpha", keys: ["network.linear", "network.linear_alpha"] },
  { label: "Captions left out", keys: ["datasets[*].caption_dropout_rate"] },
  { label: "Sample prompts", keys: [PROMPTS_KEY], prompts: true },
  {
    label: "Quantise model / text encoder",
    keys: ["model.quantize", "model.quantize_te"],
  },
  {
    label: "Low-VRAM mode · layer offloading",
    keys: ["model.low_vram", "model.layer_offloading"],
  },
  { label: "Train at sizes", keys: ["datasets[*].resolution"] },
  { label: "Optimizer / batch size", keys: ["train.optimizer", "train.batch_size"] },
  { label: "Save / sample every", keys: ["save.save_every", "sample.sample_every"] },
];

// One name per key: the field's accessible name, and a "Show more" row's label.
const KEY_LABELS = {
  "train.steps": "Steps",
  "train.lr": "Learning rate",
  "network.linear": "LoRA rank",
  "network.linear_alpha": "LoRA alpha",
  "datasets[*].caption_dropout_rate": "Captions left out",
  "model.quantize": "Quantise model",
  "model.quantize_te": "Quantise text encoder",
  "model.low_vram": "Low-VRAM mode",
  "model.layer_offloading": "Layer offloading",
  "datasets[*].resolution": "Train at sizes",
  "train.optimizer": "Optimizer",
  "train.batch_size": "Batch size",
  "save.save_every": "Save every",
  "sample.sample_every": "Sample every",
  "train.timestep_type": "Timestep type",
  "train.content_or_style": "Content or style",
  "train.cache_text_embeddings": "Encode captions once",
  "train.gradient_accumulation": "Gradient accumulation",
  "train.train_text_encoder": "Train the text encoder",
  "model.qtype": "Model quantisation",
  "model.qtype_te": "Text encoder quantisation",
  "model.layer_offloading_transformer_percent": "Model layers in RAM",
  "model.layer_offloading_text_encoder_percent": "Text encoder layers in RAM",
  "sample.sample_steps": "Sample steps",
  "sample.guidance_scale": "Sample guidance",
  "sample.width": "Sample width",
  "sample.height": "Sample height",
  "sample.seed": "Sample seed",
  "save.max_step_saves_to_keep": "Saves to keep",
  "datasets[*].num_repeats": "Repeats per picture",
  "datasets[*].flip_x": "Mirror pictures",
  "network.conv": "Conv rank",
  "network.conv_alpha": "Conv alpha",
};

/** The sizes the "Train at sizes" chips offer (all multiples of 16). */
export const SIZE_CHOICES = [512, 768, 1024, 1280, 1536];

/** A readable name for a tunable key outside the first rows. */
export function keyLabel(key) {
  return KEY_LABELS[key] || key;
}

/** Every resolved key the first rows do not show: the "Show N more" rows. */
export function moreRows(values) {
  const shown = new Set(PRIMARY_ROWS.flatMap((r) => r.keys));
  return Object.keys(values || {})
    .filter((key) => !shown.has(key))
    .sort((a, b) => keyLabel(a).localeCompare(keyLabel(b)))
    .map((key) => ({ label: keyLabel(key), keys: [key] }));
}

/** "Grandad" → "grandad", "Pub nights" → "pubnights". */
export function triggerFromName(name) {
  return String(name || "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
}

const NUMBER = new Intl.NumberFormat("en-US");

export function formatCount(n) {
  return NUMBER.format(Number(n) || 0);
}

/** Megabytes as whole gigabytes, the way the GPU choice names free memory. */
export function gb(mb) {
  return Math.round((Number(mb) || 0) / 1024);
}

/**
 * The mono line under "How hard to train": what the effort set.
 * `values` is the resolution's {key: {value, source}}; `clamp` is the
 * server's `steps_clamp` ("cap", "floor" or null).
 */
export function effortReadout({ values, clamp, effort, pictureCount }) {
  const steps = values?.["train.steps"];
  if (!steps) return "";
  const name = EFFORT_LABELS[effort] || effort;
  let stepsText = `${formatCount(steps.value)} steps`;
  if (steps.source === "pinned") {
    stepsText += " (pinned)";
  } else {
    if (clamp === "cap") {
      stepsText += ` (${name}'s cap; ${formatCount(pictureCount)} pictures)`;
    } else if (clamp === "floor") {
      stepsText += ` (${name}'s minimum; ${formatCount(pictureCount)} pictures)`;
    } else if (pictureCount > 0) {
      const per = Math.round(steps.value / pictureCount);
      stepsText += ` (about ${formatCount(per)} per picture)`;
    }
  }
  const parts = [stepsText];
  if (values["network.linear"]) parts.push(`rank ${values["network.linear"].value}`);
  if (values["train.lr"]) parts.push(`LR ${values["train.lr"].value}`);
  return parts.join(" · ");
}

/**
 * The hint under the effort read-out. `rule` is the server's `steps_rule`,
 * whose `fits` is the picture range the effort covers unclamped.
 */
export function effortHint({ rule, clamp, effort, subject }) {
  const fits = rule?.fits;
  const name = EFFORT_LABELS[effort] || effort;
  const parts = [];
  if (fits) {
    parts.push(
      clamp === "cap"
        ? `${name} suits ${fits.lo} to ${fits.hi} pictures and stops at ${formatCount(rule.max_steps)} steps for bigger sets.`
        : `${name} suits ${fits.lo} to ${fits.hi} pictures.`,
    );
  }
  if (effort === "strong") {
    parts.push(
      subject === "person"
        ? "Strong learns the face sooner and is more likely to copy backgrounds too."
        : "Strong learns it sooner and is more likely to copy the pictures too.",
    );
  } else if (effort === "gentle") {
    parts.push("Gentle stays closest to the base model.");
  }
  return parts.join(" ");
}

/**
 * The mono line under "Fit to this GPU": the memory profile it picked, its
 * peak ("at" once this machine measured it, "near" for the recipe's seed), and
 * whether previews wait until training is done (#2120).
 */
export function fitReadout(resolution) {
  if (!resolution?.profile_label) return "";
  const measured = resolution.evidence === "measured";
  const peak = measured
    ? `peaks at ${gb(resolution.vram_mb)} GB, measured`
    : `peaks near ${gb(resolution.vram_mb)} GB`;
  const previews = resolution.previews_deferred ? " · previews after training" : "";
  return `${resolution.profile_label} · ${peak}${previews}`;
}

/**
 * The footer's time line: "About 50 min." from this machine's measurements,
 * or a note that the first run of this setup checks memory and speed first.
 */
export function timeEstimate(resolution) {
  const seconds = resolution?.estimate?.seconds;
  if (resolution?.estimate?.basis === "measured" && seconds != null) {
    const m = Math.max(1, Math.round(seconds / 60));
    if (m < 60) return `About ${m} min.`;
    const rest = m % 60;
    return `About ${Math.floor(m / 60)} h${rest ? ` ${rest} min` : ""}.`;
  }
  if (resolution?.probe) return "It checks memory and speed first, so the time is not known yet.";
  return "";
}

/**
 * Where a value comes from, in the "Comes from" column's words.
 *
 * @param {{value, source}} entry The resolved value.
 * @param {object} ctx `{effort, fit, subject, character, pictureCount, clamp,
 *   presetLabel, room}`.
 */
export function sourceLabel(entry, ctx) {
  if (!entry) return "";
  const effort = EFFORT_LABELS[ctx.effort] || ctx.effort;
  switch (entry.source) {
    case "pinned":
      return "Typed by you, so it stays put";
    case "pictures":
      if (ctx.clamp === "cap") return `${effort}, capped`;
      if (ctx.clamp === "floor") return `${effort}'s minimum`;
      return `${effort} × ${formatCount(ctx.pictureCount)} pictures`;
    case "effort":
      return effort;
    case "subject":
    case "captions":
      return ctx.character ? "Character LoRA" : SUBJECT_LABELS[ctx.subject] || "";
    case "gpu":
      return `${FIT_LABELS[ctx.fit] || ctx.fit}, ${gb(ctx.room?.vram_mb)} GB free`;
    case "model":
      return ctx.presetLabel || "The recipe";
    default:
      return entry.source;
  }
}

/** Which choice a derived source belongs to, for its "N pinned · unpin". */
export function choiceOfSource(source) {
  if (source === "effort" || source === "pictures") return "effort";
  if (source === "gpu") return "fit";
  if (source === "subject" || source === "captions") return "subject";
  if (source === "model") return "model";
  return null;
}

/** A value as the Advanced field shows it. */
export function formatValue(value) {
  if (value === null || value === undefined) return "";
  return String(value);
}

/**
 * Read a typed field against its rule ({kind, lo, hi}).
 * Returns `{ value }` or `{ error }`; an empty field is `{ value: null }`.
 */
export function parseTyped(rule, text) {
  const raw = String(text ?? "").trim();
  if (!raw) return { value: null };
  const n = Number(raw);
  if (rule.kind === "int") {
    if (!Number.isInteger(n) || n < rule.lo || n > rule.hi) {
      return { error: `A whole number from ${rule.lo} to ${formatCount(rule.hi)}` };
    }
    return { value: n };
  }
  if (rule.kind === "float") {
    if (!Number.isFinite(n) || n < rule.lo || n > rule.hi) {
      return { error: `A number from ${rule.lo} to ${rule.hi}` };
    }
    return { value: n };
  }
  return { value: raw };
}

/** Whether two values are the same setting (lists by their members). */
export function sameValue(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

// ── Sample prompts ────────────────────────────────────────────────────────────

/**
 * The editable lines from GET /training/sample-prompts. Each keeps the other
 * form of its scene in `alt`, so clicking the word swaps the two.
 */
export function promptLines(items) {
  return (items || []).map((item) => ({
    text: item.prompt,
    alt:
      item.prompt === item.with_trigger ? item.without_trigger : item.with_trigger,
  }));
}

export function hasTrigger(line) {
  return line.text.includes(TRIGGER_TOKEN);
}

/** The line as the field shows it: the trigger word in place of the token. */
export function displayPrompt(text, trigger) {
  return text.split(TRIGGER_TOKEN).join(trigger || TRIGGER_TOKEN);
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** What the owner typed, with the trigger word turned back into the token. */
export function promptFromDisplay(display, trigger) {
  const word = String(trigger || "").trim();
  if (!word) return display;
  return display.replace(
    new RegExp(`(?<![\\w])${escapeRegExp(word)}(?![\\w])`, "gi"),
    TRIGGER_TOKEN,
  );
}

/** Add or leave out the trigger word on one line. */
export function toggleTrigger(line) {
  if (line.alt != null) return { text: line.alt, alt: line.text };
  if (hasTrigger(line)) {
    const text = line.text
      .split(TRIGGER_TOKEN)
      .join("")
      .replace(/\s*,\s*,/g, ",")
      .replace(/\s{2,}/g, " ")
      .replace(/^[\s,]+|[\s,]+$/g, "");
    return { text, alt: null };
  }
  const rest = line.text.trim();
  return { text: rest ? `${TRIGGER_TOKEN}, ${rest}` : TRIGGER_TOKEN, alt: null };
}

/** The prompts a run would get from these lines: the non-blank ones. */
export function promptTexts(lines) {
  return lines.map((l) => l.text.trim()).filter(Boolean);
}

/** Whether the owner's lines differ from the written ones (so they are a pin). */
export function promptsEdited(lines, defaults) {
  return !sameValue(promptTexts(lines), promptTexts(defaults));
}
