// TagImportDialog - the question an import asks before it moves tags the user
// filed by hand. Keep my filing is the default focus: Enter on a quick
// Tab-and-Space gives the safe answer, and Esc or Cancel commits nothing.

import { describe, it, expect, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import TagImportDialog from "./TagImportDialog.vue";

// The real VDialog: its own open-time focus is what Keep has to beat.
const vuetify = createVuetify({ components });

let wrapper = null;

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.innerHTML = "";
});

function mountDialog(props = {}) {
  wrapper = mount(TagImportDialog, {
    props: { count: 2, sample: ["aiko", "rin"], ...props },
    global: { plugins: [vuetify], stubs: { Tooltip: true } },
    attachTo: document.body,
  });
  return wrapper;
}

describe("TagImportDialog", () => {
  it("opens with Keep my filing focused, so a quick Enter commits nothing", async () => {
    mountDialog();
    await flushPromises();

    const keep = document.querySelector('[data-testid="tid-keep"]');
    expect(keep).not.toBeNull();
    expect(document.activeElement).toBe(keep);
  });

  it("names the count and the sample, and answers Keep, Move and Cancel", async () => {
    mountDialog();
    await flushPromises();

    expect(document.querySelector('[data-testid="tid-message"]').textContent).toContain(
      "2 tags you filed yourself are in other groups",
    );
    expect(document.querySelector(".tid-sample").textContent).toContain("aiko, rin");

    document.querySelector('[data-testid="tid-keep"]').click();
    document.querySelector('[data-testid="tid-move"]').click();
    const cancel = [...document.querySelectorAll(".app-dialog__footer button")].find(
      (b) => b.textContent.trim().startsWith("Cancel"),
    );
    cancel.click();

    expect(wrapper.emitted("keep")).toHaveLength(1);
    expect(wrapper.emitted("move")).toHaveLength(1);
    expect(wrapper.emitted("close")).toHaveLength(1);
  });
});
