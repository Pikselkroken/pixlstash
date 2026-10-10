// Which of a workflow's parameters are set each run, and which are fixed.
//
// The stored pin list (`PUT /workflows/{id}/pins`) names the parameters the
// Run form asks for; every other parameter is fixed at the workflow's value.
// `null` pins mean nobody has chosen, which is deliberately not the same as
// an empty list: the defaults below apply.

/** The numbers a person changes between runs; sampler and scheduler are not. */
export const DEFAULT_PINS = [
  "steps",
  "cfg",
  "cfg_scale",
  "guidance",
  "width",
  "height",
];

/** Whether `row` ({slot_label, input_name}) is set each run under `pins`. */
export function setEachRun(row, pins) {
  if (!Array.isArray(pins)) return DEFAULT_PINS.includes(row.input_name);
  return pins.some(
    (pin) =>
      pin.slot_label === row.slot_label && pin.input_name === row.input_name,
  );
}

/** One parameter's address, as the routes spell it: `<slot_label>/<input_name>`. */
export function parameterAddress(row) {
  return `${row.slot_label}/${row.input_name}`;
}

/**
 * `{address: [option]}` for every drop-down `GET …/form-inputs` lists: what
 * ComfyUI offers for each parameter that is a choice there.
 */
export function optionsByAddress(nodes) {
  const found = {};
  for (const node of nodes || []) {
    for (const input of node.inputs || []) {
      if (input.options?.length) found[parameterAddress(input)] = input.options;
    }
  }
  return found;
}

/**
 * The options a row's drop-down offers, or null when it is not a choice. The
 * value it holds stays among them even where this ComfyUI no longer lists it,
 * so opening the list never changes what the row says.
 */
export function choicesFor(options, row, value = row.value) {
  const listed = options?.[parameterAddress(row)];
  if (!listed) return null;
  const current = String(value);
  return listed.includes(current) ? listed : [current, ...listed];
}
