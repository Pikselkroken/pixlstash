import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import PluginParametersUI from "./PluginParametersUI.vue";

const plugin = {
  name: "fixture",
  description: "A plugin with schema-driven controls.",
  parameters: [
    {
      name: "mode",
      label: "Processing mode",
      type: "string",
      enum: ["safe", "fast"],
      description: "Choose how the plugin balances quality and speed.",
    },
    {
      name: "limit",
      label: "Result limit",
      type: "integer",
      min: 1,
      max: 20,
      step: 1,
      description: "The maximum number of results.",
    },
    {
      name: "enabled",
      label: "Run automatically",
      type: "boolean",
      description: "Runs when new pictures arrive.",
    },
    {
      name: "caption",
      label: "وصف طويل جدًا مع emoji 🖼️ and unbroken-content.example/path",
      type: "string",
    },
  ],
};

function fieldControl(field) {
  return field.find("select, input");
}

describe("PluginParametersUI accessibility hardening", () => {
  it("associates every schema label and description with its control", () => {
    const wrapper = mount(PluginParametersUI, { props: { plugin } });
    const fields = wrapper.findAll(".plugin-ui-field");

    expect(fields).toHaveLength(plugin.parameters.length);
    for (const field of fields) {
      const label = field.get(".plugin-ui-label");
      const control = fieldControl(field);
      expect(control.attributes("id")).toBeTruthy();
      expect(label.attributes("for")).toBe(control.attributes("id"));

      const help = field.find(".plugin-ui-help");
      if (help.exists()) {
        expect(control.attributes("aria-describedby")).toBe(
          help.attributes("id"),
        );
      } else {
        expect(control.attributes("aria-describedby")).toBeUndefined();
      }
    }
  });

  it("passes declared numeric constraints through to the native input", () => {
    const wrapper = mount(PluginParametersUI, { props: { plugin } });
    const number = wrapper.get('input[type="number"]');

    expect(number.attributes()).toMatchObject({
      min: "1",
      max: "20",
      step: "1",
    });
  });

  it("keeps generated control ids unique across component instances", () => {
    const wrapper = mount({
      components: { PluginParametersUI },
      data: () => ({ plugin }),
      template: `
        <div>
          <PluginParametersUI :plugin="plugin" />
          <PluginParametersUI :plugin="plugin" />
        </div>
      `,
    });
    const selectIds = wrapper.findAll("select").map((select) =>
      select.attributes("id"),
    );

    expect(new Set(selectIds).size).toBe(2);
  });
});
