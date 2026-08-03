import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import TelemetryPayloadPreview from "./TelemetryPayloadPreview.vue";

describe("TelemetryPayloadPreview", () => {
  it("renders the installation's real new-install classification", () => {
    const wrapper = mount(TelemetryPayloadPreview, {
      props: {
        variant: "id",
        installType: "pip",
        isNewInstall: false,
      },
    });

    expect(wrapper.text()).toContain('"is_new_install": false');
    expect(wrapper.text()).not.toContain('"is_new_install": true');
  });
});
