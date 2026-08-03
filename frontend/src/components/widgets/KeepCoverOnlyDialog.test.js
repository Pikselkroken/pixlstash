// The Keep-cover-only confirm is the single consent for hundreds of soft
// deletions, so these tests pin what the user is promised before they give it:
// one figure that the button cannot contradict, no number at all until the
// preview lands, a zero for disk stated out loud, a retention sentence read
// from the server, and a keyboard that deliberately refuses Enter.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

import KeepCoverOnlyDialog from "./KeepCoverOnlyDialog.vue";
import AppButton from "./AppButton.vue";

// The real AppDialog renders through Vuetify's overlay teleport, which puts the
// footer out of the wrapper's reach. This stub keeps the slots in place and, in
// `onKeydown`, reproduces the shipped dialog keyboard contract verbatim so the
// Enter test is measuring the real rule rather than a convenient one.
const AppDialogStub = {
  name: "AppDialog",
  props: { open: Boolean, title: String, width: Number },
  emits: ["close", "accept"],
  template: `<div class="dlg" @keydown="onKeydown">
      <h2 class="dlg-title">{{ title }}</h2>
      <slot />
      <div class="dlg-footer"><slot name="footer" /></div>
    </div>`,
  methods: {
    onKeydown(e) {
      if (e.key === "Escape") return this.$emit("close");
      if (e.key !== "Enter") return;
      const exempt =
        "textarea, select, button, a[href], summary, [contenteditable='true'], [role='textbox']";
      if (e.target instanceof Element && e.target.closest(exempt)) return;
      this.$emit("accept");
    },
  },
};

const globalOpts = {
  global: { stubs: { "v-icon": true, AppDialog: AppDialogStub } },
};

/**
 * A dry run over 20 stacks: 17 collapse, 3 are refused for three different
 * reasons, and the buckets already sum to `stacks_selected`.
 */
const PREVIEW = {
  stacks_selected: 20,
  stacks_eligible: 17,
  stacks_skipped_locked: 2,
  stacks_skipped_character_on_copy: 1,
  stacks_skipped_single_member: 0,
  pictures_moving: 414,
  covers_kept: 17,
  covers_gaining_metadata: 12,
  reference_folder_pictures_moving: 5,
  bytes_held_by_copies: 1_234_567_890,
  originals_deleted_from_disk: 0,
  scrapheap_retention_days: null,
};

function mountDialog(props = {}) {
  return mount(KeepCoverOnlyDialog, {
    ...globalOpts,
    props: { open: true, preview: PREVIEW, ...props },
  });
}

/** The footer buttons, in render order: Cancel, Move N to the Scrapheap. */
function footerButtons(wrapper) {
  return wrapper.findAllComponents(AppButton);
}

/** Every digit run in a string, so two renderings can be compared numerically. */
function digitsIn(text) {
  return (text.match(/[\d,]+/g) || []).map((n) => n.replace(/,/g, ""));
}

describe("KeepCoverOnlyDialog: one figure, two places", () => {
  // The whole point of the component. The neighbouring auto-stack dialog once
  // reported "62 stacks to create" for work that would create 3 because two
  // renderings read two different things; here they read one computed.
  it("renders the headline and the button from the same number", () => {
    const wrapper = mountDialog();
    const figure = wrapper.get('[data-testid="keep-cover-figure"]').text();
    const button = footerButtons(wrapper)[1].text();
    expect(figure).toBe((414).toLocaleString());
    expect(button).toContain(`Move ${(414).toLocaleString()} to the Scrapheap`);
    expect(digitsIn(button)).toEqual(digitsIn(figure));
  });

  it("still agrees when the server reports a different total", async () => {
    const wrapper = mountDialog();
    await wrapper.setProps({
      preview: { ...PREVIEW, pictures_moving: 7, stacks_eligible: 2 },
    });
    const figure = wrapper.get('[data-testid="keep-cover-figure"]').text();
    expect(digitsIn(footerButtons(wrapper)[1].text())).toEqual(digitsIn(figure));
    expect(figure).toBe("7");
  });

  // Title names what survives; the button names what goes. Two different
  // figures on purpose, and the title's is the eligible-stack count, never the
  // selected one, which would promise work on stacks that are being skipped.
  it("titles with what you keep, not with what was selected", () => {
    const wrapper = mountDialog();
    expect(wrapper.get(".dlg-title").text()).toBe(
      "Keep only the cover of 17 stacks",
    );
  });
});

describe("KeepCoverOnlyDialog: before the numbers land", () => {
  for (const [name, props] of [
    ["while the preview is in flight", { preview: null, loading: true }],
    ["when the preview could not be read", { preview: null, previewFailed: true }],
  ]) {
    it(`shows no number and refuses to confirm ${name}`, () => {
      const wrapper = mountDialog(props);
      const figure = wrapper.get('[data-testid="keep-cover-figure"]');
      // An en dash at the figure's own size, so the dialog keeps its height and
      // the confirm button does not move under the pointer when counts land.
      expect(figure.text()).toBe("–");
      const button = footerButtons(wrapper)[1];
      expect(digitsIn(button.text())).toEqual([]);
      expect(button.find("button").attributes("disabled")).toBeDefined();
    });
  }

  it("never shows a stale figure from the previous selection", async () => {
    const wrapper = mountDialog();
    expect(wrapper.get('[data-testid="keep-cover-figure"]').text()).toBe(
      (414).toLocaleString(),
    );
    await wrapper.setProps({ preview: null, loading: true });
    expect(wrapper.get('[data-testid="keep-cover-figure"]').text()).toBe("–");
  });

  // A failed preview and a genuinely empty one must not be the same screen.
  it("says so when the preview could not be read", () => {
    expect(mountDialog({ preview: null, previewFailed: true }).find(".kco-failed").exists()).toBe(true);
    expect(mountDialog().find(".kco-failed").exists()).toBe(false);
  });

  // Zero is a real answer, not a placeholder, and it still must not be
  // confirmable: there is nothing to do.
  it("shows a genuine zero as zero, with the confirm still disabled", () => {
    const wrapper = mountDialog({
      preview: { ...PREVIEW, pictures_moving: 0, stacks_eligible: 0 },
    });
    expect(wrapper.get('[data-testid="keep-cover-figure"]').text()).toBe("0");
    expect(
      footerButtons(wrapper)[1].find("button").attributes("disabled"),
    ).toBeDefined();
  });
});

describe("KeepCoverOnlyDialog: nothing is freed", () => {
  it("states the zero originals deleted from disk out loud", () => {
    const rows = mountDialog().findAll(".kco-row");
    expect(rows).toHaveLength(5);
    expect(rows[4].find(".kco-term").text()).toContain(
      "Originals deleted from disk",
    );
    expect(rows[4].find(".kco-value").text()).toBe("0");
  });

  // Stated even while the counts are unknown: it is a property of the feature,
  // not a number the server has to be asked for.
  it("keeps stating it while the preview is in flight", () => {
    const rows = mountDialog({ preview: null, loading: true }).findAll(
      ".kco-row",
    );
    expect(rows.map((r) => r.find(".kco-value").text())).toEqual([
      "–",
      "–",
      "–",
      "–",
      "0",
    ]);
  });

  it("keeps the byte figure a sentence and never claims space was freed", () => {
    const text = mountDialog().find(".kco-recovery").text();
    expect(text).toContain("1.1 GB");
    expect(text).not.toMatch(/free|reclaim|saved/i);
    // A figure block is for what changes now; this changes later, if ever.
    expect(mountDialog().findAll(".kco-figure")).toHaveLength(1);
  });

  it("reports the reference-folder rows without claiming their files moved", () => {
    expect(mountDialog().find(".kco-recovery").text()).toContain(
      "the files stay exactly where they are",
    );
  });
});

describe("KeepCoverOnlyDialog: the retention window is read, not assumed", () => {
  it("says the Scrapheap never empties on its own by default", () => {
    const text = mountDialog().find(".kco-recovery").text();
    expect(text).toContain("never empties on its own");
    expect(text).not.toContain("30 days");
  });

  it("names the configured window when the server carries one", () => {
    const text = mountDialog({
      preview: { ...PREVIEW, scrapheap_retention_days: 60 },
    })
      .find(".kco-recovery")
      .text();
    expect(text).toContain("after 60 days");
    expect(text).not.toContain("never empties");
  });

  it("falls back to never when the server does not report the setting", () => {
    const preview = { ...PREVIEW };
    delete preview.scrapheap_retention_days;
    expect(mountDialog({ preview }).find(".kco-recovery").text()).toContain(
      "never empties on its own",
    );
  });
});

describe("KeepCoverOnlyDialog: the skips", () => {
  it("counts skipped stacks by summing the buckets, and names them", () => {
    const wrapper = mountDialog();
    const rows = wrapper.findAll(".kco-row");
    expect(rows[3].find(".kco-term").text()).toContain("Stacks skipped");
    expect(rows[3].find(".kco-value").text()).toBe("3");
    const skips = wrapper.findAll(".kco-skips li").map((li) => li.text());
    expect(skips).toHaveLength(2);
    // A locked set refuses the WHOLE stack; the dialog has to say so, because a
    // partial collapse is the worst outcome available and was not attempted.
    expect(skips[0]).toContain("whole");
  });

  it("says nothing about skips when there are none", () => {
    const wrapper = mountDialog({
      preview: {
        ...PREVIEW,
        stacks_skipped_locked: 0,
        stacks_skipped_character_on_copy: 0,
      },
    });
    expect(wrapper.find(".kco-skips").exists()).toBe(false);
    expect(wrapper.findAll(".kco-row")[3].find(".kco-value").text()).toBe("0");
  });
});

describe("KeepCoverOnlyDialog: the keyboard, deliberately inverted", () => {
  // Users arrive here from the duplicate queue with Enter under their finger
  // from the verdict keys. The next press must not be consent.
  it("does not accept on a plain Enter", async () => {
    const wrapper = mountDialog();
    await wrapper.get(".dlg").trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("confirm")).toBeUndefined();
  });

  it("puts the keyboard on Cancel when it opens", async () => {
    // Attached so focus() actually moves document.activeElement.
    const wrapper = mount(KeepCoverOnlyDialog, {
      ...globalOpts,
      attachTo: document.body,
      props: { open: false, preview: PREVIEW },
    });
    const cancel = footerButtons(wrapper)[0].find("button").element;
    await wrapper.setProps({ open: true });
    await vi.waitFor(() => expect(document.activeElement).toBe(cancel));
    wrapper.unmount();
  });

  // With Cancel focused, Enter reaches a native button and activates it. The
  // dialog therefore dismisses on Enter rather than confirming, which is the
  // point of focusing Cancel in the first place.
  it("dismisses rather than confirms when Enter lands on Cancel", async () => {
    const wrapper = mountDialog();
    const cancel = footerButtons(wrapper)[0].find("button");
    await cancel.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("confirm")).toBeUndefined();
    await cancel.trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  // The heavier ceremony belongs to DeleteForeverDialog, where an on-disk
  // original dies. Borrowing it here would flatten the distinction between
  // "recoverable" and "gone".
  it("asks for no typed confirmation", () => {
    const wrapper = mountDialog();
    expect(wrapper.find("input").exists()).toBe(false);
    expect(wrapper.text()).not.toMatch(/type\s+DELETE/i);
  });
});

describe("KeepCoverOnlyDialog: the confirmation", () => {
  it("emits confirm once the figures are real", async () => {
    const wrapper = mountDialog();
    await footerButtons(wrapper)[1].find("button").trigger("click");
    expect(wrapper.emitted("confirm")).toHaveLength(1);
  });

  it("cannot fire twice while the run is in flight", () => {
    const wrapper = mountDialog({ busy: true });
    expect(
      footerButtons(wrapper)[1].find("button").attributes("disabled"),
    ).toBeDefined();
  });

  it("promises the copies come back and spells the undo out in keycaps", () => {
    const wrapper = mountDialog();
    expect(wrapper.find(".kco-lede").text()).toContain("you can restore it");
    expect(wrapper.findAll(".kco-reversible kbd").map((k) => k.text())).toEqual([
      "Ctrl",
      "Z",
    ]);
  });

  // The one place the action does more than the selection literally names.
  it("says a stack collapses whole and loose pictures are left alone", () => {
    const lede = mountDialog().find(".kco-lede").text();
    expect(lede).toContain("collapses whole");
    expect(lede).toContain("loose pictures are left alone");
  });
});
