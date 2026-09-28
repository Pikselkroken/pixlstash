/**
 * What a saved recipe changes against its workflow's default recipe (#1653,
 * docs/design/workflow-inspector-two-layers.md §1.3).
 *
 * The Recipes tab prints this instead of every LoRA chip: a card states only
 * what differs, in one fixed order - models, added LoRAs, strength changes,
 * removed default LoRAs, parameters, seed. Each segment is a list of parts, so
 * the view can put the arrow in the quiet ink and the names in full ink.
 *
 * The negative prompt is never a segment: the default recipe holds no prompt,
 * negative or seed, so there is nothing to differ from.
 */

/** Shelf kinds as the line prefixes them; a checkpoint goes unprefixed. */
const KIND_LABELS = { vae: "VAE", text_encoder: "Text encoder" };

/** "characters/Ada_v2.safetensors" → "Ada_v2". */
export function shortName(filename) {
  const base = String(filename ?? "").split(/[\\/]/).pop();
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(0, dot) : base;
}

/** A strength as a person writes it: 0.8, 1, 0.85. A missing one loads at 1. */
function strengthText(value) {
  const number = Number(value ?? 1);
  return String(Number((Number.isFinite(number) ? number : 1).toFixed(2)));
}

function sameValue(a, b) {
  const x = Number(a);
  const y = Number(b);
  if (a !== "" && b !== "" && Number.isFinite(x) && Number.isFinite(y)) {
    return x === y;
  }
  return String(a) === String(b);
}

function valueText(value) {
  return typeof value === "boolean" ? (value ? "on" : "off") : String(value);
}

/**
 * One load of a file is the same file as another's when both digests agree,
 * or (where either side cannot name its digest) the short names do.
 */
function sameFile(a, b) {
  if (a.sha256 && b.sha256) return a.sha256.toLowerCase() === b.sha256.toLowerCase();
  return shortName(a.filename).toLowerCase() === shortName(b.filename).toLowerCase();
}

/**
 * Pair two LoRA lists as multisets: each load is matched at most once, equal
 * strengths first, so one file loaded twice pairs with its own strengths.
 */
/**
 * Whether two LoRA lists can be compared at all. A LoRA is matched by digest
 * when both sides carry one, else by file name; a LoRA with neither on the
 * other side's terms (a default LoRA the owner added has no filename, a look
 * carries no digests) cannot be paired, and comparing anyway prints "+ X" and
 * a nameless "without" for what is really one LoRA.
 */
function comparable(mine, theirs) {
  const allSha = (list) => list.every((lora) => lora.sha256);
  const ok = (lora, other) => Boolean(lora.filename) || (lora.sha256 && allSha(other));
  return mine.every((lora) => ok(lora, theirs)) && theirs.every((lora) => ok(lora, mine));
}

function pairLoras(mine, theirs) {
  const left = mine.map((lora) => ({ lora, with: null }));
  const free = theirs.map((lora) => ({ lora, taken: false }));
  for (const exact of [true, false]) {
    for (const row of left) {
      if (row.with) continue;
      const match = free.find(
        (other) =>
          !other.taken &&
          sameFile(row.lora, other.lora) &&
          (!exact || strengthText(row.lora.strength) === strengthText(other.lora.strength)),
      );
      if (match) {
        match.taken = true;
        row.with = match.lora;
      }
    }
  }
  return {
    pairs: left.filter((row) => row.with),
    added: left.filter((row) => !row.with).map((row) => row.lora),
    removed: free.filter((row) => !row.taken).map((row) => row.lora),
  };
}

/**
 * A LoRA's name on the line. A default LoRA the owner added is stored by
 * digest with no filename, and pairs by digest when the recipe has them, so a
 * recipe can really be without one; it is named as unnamed rather than
 * printed as a blank "without ".
 */
function loraName(lora) {
  return shortName(lora.filename) || "an unnamed LoRA";
}

function segment(kind, parts) {
  const list = parts.map((part) => (typeof part === "string" ? { text: part } : part));
  return { kind, parts: list, text: list.map((part) => part.text).join("") };
}

const ARROW = { text: " → ", quiet: true };

/** `core:Sampler/steps` → ["core:Sampler", "steps"]. */
function splitAddress(address) {
  const at = address.lastIndexOf("/");
  return at < 0 ? ["", address] : [address.slice(0, at), address.slice(at + 1)];
}

/**
 * The default row an override fills. By exact address first; else by the
 * input it names, when exactly one default row takes that input (an older
 * override may carry the base topology's slot label, not the `core:` one).
 */
function defaultRowFor(address, rows) {
  const [slot, input] = splitAddress(address);
  const exact = rows.find((row) => row.slot_label === slot && row.input_name === input);
  if (exact) return exact;
  const byInput = rows.filter((row) => row.input_name === input);
  return byInput.length === 1 ? byInput[0] : null;
}

/**
 * The saved recipe against the default recipe.
 *
 * @param {Object} recipe a saved recipe (`GET /recipes`)
 * @param {Object|null} defaultRecipe `card.default_recipe` from `GET /workflows/{id}`
 * @returns {{segments: Array, params: Array}|null} null when there is no
 *   default to compare with. `params` are the parameter differences that
 *   resolve to a default row, addressed as the default is, for
 *   `PUT /workflows/{id}/defaults`.
 */
export function recipeDiff(recipe, defaultRecipe) {
  if (!recipe || !defaultRecipe) return null;
  const segments = [];

  // Models: NULL inherits the default's, so says nothing.
  const defaultModels = defaultRecipe.models || [];
  for (const model of recipe.models || []) {
    if (!model?.filename) continue;
    const base = defaultModels.find((row) => row.address === model.address);
    if (base?.filename && shortName(base.filename).toLowerCase() === shortName(model.filename).toLowerCase()) {
      continue;
    }
    const prefix = KIND_LABELS[base?.kind];
    segments.push(segment("model", [`${prefix ? `${prefix}: ` : ""}${shortName(model.filename)}`]));
  }

  // LoRAs: an empty list runs the default's, so it differs in none of them.
  const mine = recipe.loras || [];
  if (mine.length && !comparable(mine, defaultRecipe.loras || [])) {
    segments.push(segment("lora-unknown", [{ text: "LoRAs not compared", quiet: true }]));
  } else if (mine.length) {
    const { pairs, added, removed } = pairLoras(mine, defaultRecipe.loras || []);
    for (const lora of added) {
      segments.push(
        segment("lora-add", [`+ ${loraName(lora)} ${strengthText(lora.strength)}`]),
      );
    }
    for (const { lora, with: base } of pairs) {
      const was = strengthText(base.strength);
      const now = strengthText(lora.strength);
      if (was === now) continue;
      segments.push(
        segment("lora-strength", [`${loraName(lora)} ${was}`, ARROW, now]),
      );
    }
    for (const lora of removed) {
      segments.push(segment("lora-without", [`without ${loraName(lora)}`]));
    }
  }

  // Parameters, compared by the input they fill.
  const rows = defaultRecipe.values || [];
  const params = [];
  for (const [address, value] of Object.entries(recipe.overrides || {})) {
    const row = defaultRowFor(address, rows);
    if (row && sameValue(row.value, value)) continue;
    const name = row?.label || splitAddress(address)[1];
    segments.push(segment("param", [`${name} ${valueText(value)}`]));
    if (row) {
      params.push({
        slot_label: row.slot_label,
        input_name: row.input_name,
        label: row.label || row.input_name,
        from: valueText(row.value),
        to: value,
      });
    }
  }

  if (recipe.keep_seed && recipe.seed != null && recipe.seed !== "") {
    segments.push(segment("seed", [`seed ${recipe.seed}`]));
  }

  return { segments, params };
}

/**
 * A look from the pictures against the default's LoRAs (§1.3): `/recipes/used`
 * names LoRA files only, so this is added and without, by short name.
 */
export function lookLoraDiff(look, defaultRecipe) {
  if (!defaultRecipe) return null;
  const mine = (look?.loras || []).map((lora) => ({ filename: lora.filename }));
  const theirs = (defaultRecipe.loras || []).map((lora) => ({ filename: lora.filename }));
  // Looks carry no digests: a nameless default LoRA cannot be matched, so the
  // look keeps its plain LoRA list.
  if (!comparable(mine, theirs)) return null;
  const { added, removed } = pairLoras(mine, theirs);
  return [
    ...added.map((lora) => segment("lora-add", [`+ ${shortName(lora.filename)}`])),
    ...removed.map((lora) => segment("lora-without", [`without ${shortName(lora.filename)}`])),
  ];
}
