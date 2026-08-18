import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import TaggerParametersUI from "./TaggerParametersUI.vue";

const schema = [
  {
    name: "model",
    label: "Model",
    type: "select",
    enum: ["small", "large"],
    description: "Select a model.",
  },
  {
    name: "threshold",
    label: "Confidence threshold",
    type: "number",
    scale: 100,
    unit: "%",
    min: 0,
    max: 1,
    step: 0.01,
    description: "Only keep labels above this score.",
  },
  {
    name: "enabled",
    label: "Tag automatically",
    type: "bool",
    description: "Runs when pictures arrive.",
  },
  {
    name: "prompt",
    label: "تعليمات مفصلة جدًا 🧭",
    type: "textarea",
  },
  { name: "classes", label: "Class IDs", type: "csv-int" },
  { name: "prefix", label: "Prefix", type: "string" },
];

function fieldControl(field) {
  return field.find("select, input, textarea");
}

describe("TaggerParametersUI accessibility hardening", () => {
  it("associates every schema label and available help text", () => {
    const wrapper = mount(TaggerParametersUI, { props: { schema } });
    const fields = wrapper.findAll(".tagger-params-field");

    expect(fields).toHaveLength(schema.length);
    for (const field of fields) {
      const label = field.get(".tagger-params-label");
      const control = fieldControl(field);
      expect(label.attributes("for")).toBe(control.attributes("id"));

      const help = field.find(".tagger-params-help");
      if (help.exists()) {
        expect(control.attributes("aria-describedby").split(" ")).toContain(
          help.attributes("id"),
        );
      }
    }
  });

  it("describes a scaled number with both its unit and help text", () => {
    const wrapper = mount(TaggerParametersUI, { props: { schema } });
    const field = wrapper.findAll(".tagger-params-field")[1];
    const input = field.get("input");
    const ids = input.attributes("aria-describedby").split(" ");

    expect(ids).toEqual([
      field.get(".tagger-params-unit").attributes("id"),
      field.get(".tagger-params-help").attributes("id"),
    ]);
  });
});
