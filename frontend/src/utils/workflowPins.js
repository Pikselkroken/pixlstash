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
