// The set panel's check and verdict UI (docs/ideas/workflow-set-verdicts.md).
// The server decides every state and every suspect; these tests hold the panel
// to wording what it is given and asking only when it should.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";

vi.mock("vuetify/components", async (importOriginal) => ({
  ...(await importOriginal()),
  VIcon: { name: "VIcon", template: "<i><slot /></i>" },
  VTooltip: {
    name: "VTooltip",
    setup:
      (_p, { slots }) =>
      () =>
        slots.activator?.({ props: {} }),
  },
}));
vi.mock("../../composables/useWorkflowNames", () => ({
  useWorkflowNames: () => ({ nameOf: () => "" }),
}));
vi.mock("../../api/modelIcons", () => ({
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));

import ModelSetPanel from "./ModelSetPanel.vue";

const member = (id, name, extra = {}) => ({
  id,
  name,
  filename: `${name}.safetensors`,
  kindLabel: "LoRA",
  recipes: 1,
  pictures: 2,
  otherSets: 0,
  head: id === 1,
  ...extra,
});
const MEMBERS = [member(1, "Base"), member(2, "Lora"), member(3, "Style")];

const entry = (check, extra = {}) => ({
  combo_key: "1,2,3",
  member_ids: [1, 2, 3],
  evidence: { together: 6, checked: 5, passing: 4 },
  check,
  verdict: null,
  suspects: [],
  member_verdicts: [],
  ...extra,
});

const suspect = (id, extra = {}) => ({
  model_id: id,
  with_failed: 4,
  with_total: 5,
  without_failed: 1,
  without_total: 9,
  verdict: null,
  ...extra,
});

const saveSet = vi.fn();
const saveMember = vi.fn();
beforeEach(() => {
  saveSet.mockReset().mockResolvedValue();
  saveMember.mockReset().mockResolvedValue();
});

function mountPanel(props = {}) {
  return mount(ModelSetPanel, {
    props: {
      panelId: "p",
      name: "Base",
      members: MEMBERS,
      saveSet,
      saveMember,
      ...props,
    },
    attachTo: document.body,
    global: { stubs: { "router-link": true } },
  });
}

const note = (w) => w.get('[data-testid="model-set-note"]').text();
const buttonNames = (w) =>
  w
    .findAll('button[data-testid^="verdict-"]')
    .map((b) => b.attributes("aria-label"));

describe("ModelSetPanel check note", () => {
  it("states plain no-evidence copy with no entry, and draws no check or verdict UI", () => {
    const w = mountPanel({ check: null });
    expect(note(w)).toContain("Each of these has run with Base.");
    expect(note(w)).toContain("open a model and read what it works with.");
    expect(note(w)).not.toContain("—");
    expect(w.find('[data-testid="verdict-ask"]').exists()).toBe(false);
    w.unmount();
  });

  it("keeps the no-evidence copy for check none", () => {
    const w = mountPanel({ check: entry("none") });
    expect(note(w)).toContain("Each of these has run with Base.");
    expect(w.find('[data-testid="verdict-ask"]').exists()).toBe(false);
    w.unmount();
  });

  it("adds the unavailable sentence", () => {
    const w = mountPanel({ check: entry("unavailable") });
    expect(note(w)).toContain("Each of these has run with Base.");
    expect(note(w)).toContain("PixlStash has not checked these pictures.");
    w.unmount();
  });

  it("says pending with the done count and does not ask", () => {
    const w = mountPanel({
      check: entry("pending", {
        evidence: { together: 6, checked: 2, passing: 1 },
      }),
    });
    expect(note(w)).toContain("still checking them (2 of 6 done)");
    expect(w.find('[data-testid="verdict-ask"]').exists()).toBe(false);
    w.unmount();
  });

  it("says too_few with the count line and does not ask", () => {
    const w = mountPanel({ check: entry("too_few") });
    expect(note(w)).toContain(
      "These have all run together, in 6 pictures, 4 of which look like their prompt (PixlStash's automatic check).",
    );
    expect(w.find('[data-testid="verdict-ask"]').exists()).toBe(false);
    w.unmount();
  });

  it("asks on pass with two named buttons", () => {
    const w = mountPanel({ check: entry("pass") });
    expect(note(w)).toContain(
      "PixlStash thinks this set produces sensible output. Do you agree?",
    );
    expect(buttonNames(w)).toEqual([
      "Yes, this set produces sensible output",
      "No, this set does not produce sensible output",
    ]);
    w.unmount();
  });

  it("asks on fail, with the caveat", () => {
    const w = mountPanel({ check: entry("fail") });
    expect(note(w)).toContain(
      "PixlStash thinks this set does not produce sensible output. Do you agree?",
    );
    expect(note(w)).toContain(
      "This catches noise and unrelated output, not style or quality.",
    );
    w.unmount();
  });

  it("emits the answer and announces it in the live region mounted before it", async () => {
    const w = mountPanel({ check: entry("pass") });
    const live = w.get('[data-testid="model-set-live"]');
    expect(live.attributes("role")).toBe("status");
    expect(live.text()).toBe("");
    await w.get('[data-testid="verdict-yes"]').trigger("click");
    await nextTick();
    await nextTick();
    expect(saveSet).toHaveBeenCalledWith("yes");
    expect(live.text()).toBe("Recorded: this set produces sensible output");
    w.unmount();
  });

  it("shows the answered verdict as one icon with the verdict in its name", () => {
    const w = mountPanel({ check: entry("fail", { verdict: "no" }) });
    const icon = w.get('[data-testid="verdict-icon"]');
    expect(icon.attributes("aria-label")).toBe(
      "Your verdict: this set does not produce sensible output. Click to change.",
    );
    expect(w.find('[data-testid="verdict-yes"]').exists()).toBe(false);
    w.unmount();
  });
});

describe("ModelSetPanel suspects", () => {
  it("asks about a suspect with the payload's counts and names", async () => {
    const w = mountPanel({ check: entry("pass", { suspects: [suspect(2)] }) });
    const text = w.get('[data-testid="model-set-suspect"]').text();
    expect(text).toContain("Lora may be a problem.");
    expect(text).toContain(
      "4 of 5 pictures made with it don't look like their prompt, against 1 of 9 without it.",
    );
    expect(buttonNames(w)).toContain("Yes, Lora is a problem in this set");
    expect(buttonNames(w)).toContain("No, Lora is not a problem in this set");
    await w.get('[data-testid="verdict-problem"]').trigger("click");
    await nextTick();
    await nextTick();
    expect(saveMember).toHaveBeenCalledWith(2, "problem");
    expect(w.get('[data-testid="model-set-live"]').text()).toBe(
      "Recorded: Lora is a problem in this set",
    );
    w.unmount();
  });

  it("flags only what the payload lists: no suspects, no suspect UI", () => {
    const w = mountPanel({ check: entry("pass") });
    expect(w.find('[data-testid="model-set-suspect"]').exists()).toBe(false);
    expect(w.text()).not.toContain("Possible problem");
    w.unmount();
  });

  it("shows three notes and counts the rest", () => {
    const members = [1, 2, 3, 4, 5, 6].map((id) => member(id, `M${id}`));
    const w = mountPanel({
      members,
      check: entry("pass", {
        suspects: [2, 3, 4, 5, 6].map((id) => suspect(id)),
      }),
    });
    expect(w.findAll('[data-testid="model-set-suspect"]')).toHaveLength(3);
    expect(w.get('[data-testid="model-set-suspects-more"]').text()).toContain(
      "2 more models flagged",
    );
    w.unmount();
  });

  it("pills an unanswered suspect, and an answered member wears its icon instead", () => {
    const w = mountPanel({
      check: entry("pass", {
        suspects: [suspect(2), suspect(3, { verdict: "not_problem" })],
        member_verdicts: [{ model_id: 3, verdict: "not_problem" }],
      }),
    });
    const cards = w.findAll('[data-testid="model-set-member"]');
    expect(cards[1].text()).toContain("Possible problem");
    expect(cards[2].text()).not.toContain("Possible problem");
    const icon = cards[2].get('[data-testid="verdict-icon"]');
    expect(icon.attributes("aria-label")).toBe(
      "Your verdict: Style is not a problem in this set",
    );
    expect(cards[1].find('[data-testid="verdict-icon"]').exists()).toBe(false);
    w.unmount();
  });
});

describe("ModelSetPanel refused saves", () => {
  it("shows an alert beside the set question and keeps it open", async () => {
    saveSet.mockRejectedValue(new Error("403"));
    const w = mountPanel({ check: entry("pass") });
    await w.get('[data-testid="verdict-yes"]').trigger("click");
    await flushPromises();
    const alert = w.get('[role="alert"]');
    expect(alert.text()).toContain("Could not save your answer. Try again.");
    expect(w.find('[data-testid="verdict-yes"]').exists()).toBe(true);
    expect(w.get('[data-testid="model-set-live"]').text()).toBe(
      "Could not save your answer. Try again.",
    );
    // No icon: nothing was recorded.
    expect(w.find('[data-testid="verdict-icon"]').exists()).toBe(false);
    w.unmount();
  });

  it("clears the alert when the next try is accepted", async () => {
    saveSet.mockRejectedValueOnce(new Error("403"));
    const w = mountPanel({ check: entry("pass") });
    await w.get('[data-testid="verdict-yes"]').trigger("click");
    await flushPromises();
    expect(w.find('[role="alert"]').exists()).toBe(true);
    await w.get('[data-testid="verdict-yes"]').trigger("click");
    await flushPromises();
    expect(w.find('[role="alert"]').exists()).toBe(false);
    expect(w.get('[data-testid="model-set-live"]').text()).toBe(
      "Recorded: this set produces sensible output",
    );
    w.unmount();
  });

  it("reopens an answered question when changing it is refused", async () => {
    saveSet.mockRejectedValue(new Error("500"));
    const w = mountPanel({ check: entry("pass", { verdict: "yes" }) });
    await w.get('[data-testid="verdict-icon"]').trigger("click");
    await w.get('[data-testid="verdict-no"]').trigger("click");
    await flushPromises();
    expect(w.get('[role="alert"]').exists()).toBe(true);
    expect(
      w.get('[data-testid="verdict-yes"]').attributes("aria-pressed"),
    ).toBe("true");
    w.unmount();
  });

  it("shows the alert on a suspect note too, and clears it on the next try", async () => {
    saveMember.mockRejectedValueOnce(new Error("x"));
    const w = mountPanel({ check: entry("pass", { suspects: [suspect(2)] }) });
    await w.get('[data-testid="verdict-problem"]').trigger("click");
    await flushPromises();
    expect(w.findAll('[role="alert"]')).toHaveLength(1);
    await w.get('[data-testid="verdict-problem"]').trigger("click");
    await flushPromises();
    expect(w.find('[role="alert"]').exists()).toBe(false);
    expect(saveMember).toHaveBeenCalledTimes(2);
    w.unmount();
  });
});

describe("ModelSetPanel member icons change the answer", () => {
  const answered = (verdict = "not_problem") =>
    entry("pass", { member_verdicts: [{ model_id: 3, verdict }] });

  it("reopens a non-suspect member's question from its card icon", async () => {
    const w = mountPanel({ check: answered() });
    expect(w.find('[data-testid="model-set-suspect"]').exists()).toBe(false);
    const cardIcon = w
      .findAll('[data-testid="model-set-member"]')[2]
      .get('[data-testid="verdict-icon"]');
    expect(cardIcon.element.tagName).toBe("BUTTON");
    await cardIcon.trigger("click");
    await flushPromises();
    const note = w.get('[data-testid="model-set-suspect"]');
    expect(note.text()).toContain("Is Style a problem in this set?");
    const no = note.get('[data-testid="verdict-not_problem"]');
    expect(no.attributes("aria-pressed")).toBe("true");
    expect(note.text()).toContain("it changes nothing else.");
    await note.get('[data-testid="verdict-problem"]').trigger("click");
    await flushPromises();
    expect(saveMember).toHaveBeenCalledWith(3, "problem");
    w.unmount();
  });

  it("Cancel removes the extra note and returns focus to the member's icon", async () => {
    const w = mountPanel({ check: answered() });
    const icon = w
      .findAll('[data-testid="model-set-member"]')[2]
      .get('[data-testid="verdict-icon"]');
    await icon.trigger("click");
    await flushPromises();
    await w.get('[data-testid="verdict-cancel"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(w.find('[data-testid="model-set-suspect"]').exists()).toBe(false);
    expect(document.activeElement).toBe(icon.element);
    w.unmount();
  });

  it("reopens a suspect's own note rather than adding a second one", async () => {
    const w = mountPanel({
      check: entry("pass", {
        suspects: [suspect(3, { verdict: "problem" })],
        member_verdicts: [{ model_id: 3, verdict: "problem" }],
      }),
    });
    await w
      .findAll('[data-testid="model-set-member"]')[2]
      .get('[data-testid="verdict-icon"]')
      .trigger("click");
    await flushPromises();
    expect(w.findAll('[data-testid="model-set-suspect"]')).toHaveLength(1);
    expect(
      w.get('[data-testid="verdict-problem"]').attributes("aria-pressed"),
    ).toBe("true");
    w.unmount();
  });

  it("makes the List row's icon a button too, outside the hidden cells", async () => {
    const w = mountPanel({ view: "list", check: answered("problem") });
    const icon = w.get('.msp__row [data-testid="verdict-icon"]');
    expect(icon.element.tagName).toBe("BUTTON");
    expect(icon.element.closest('[aria-hidden="true"]')).toBeNull();
    await icon.trigger("click");
    await flushPromises();
    expect(
      w.get('[data-testid="verdict-problem"]').attributes("aria-pressed"),
    ).toBe("true");
    w.unmount();
  });
});

describe("ModelSetPanel answers line (keyboard route)", () => {
  const lineButtons = (w) =>
    w.findAll('[data-testid="model-set-answer"] button');

  it("appears only when a member has a verdict", () => {
    const none = mountPanel({ check: entry("pass") });
    expect(none.find('[data-testid="model-set-answers"]').exists()).toBe(false);
    none.unmount();
    const some = mountPanel({
      check: entry("pass", {
        member_verdicts: [
          { model_id: 2, verdict: "problem" },
          { model_id: 3, verdict: "not_problem" },
        ],
      }),
    });
    expect(some.get('[data-testid="model-set-answers"]').text()).toContain(
      "Your answers about models in this set:",
    );
    expect(lineButtons(some).map((b) => b.attributes("aria-label"))).toEqual([
      "Lora: your verdict, a problem in this set. Change",
      "Style: your verdict, not a problem in this set. Change",
    ]);
    // Reachable by Tab: no tabindex of -1.
    expect(lineButtons(some)[0].attributes("tabindex")).not.toBe("-1");
    some.unmount();
  });

  it("each button reopens its own member's question, and Cancel returns focus to it", async () => {
    const w = mountPanel({
      check: entry("pass", {
        member_verdicts: [
          { model_id: 2, verdict: "problem" },
          { model_id: 3, verdict: "not_problem" },
        ],
      }),
    });
    const second = lineButtons(w)[1];
    await second.trigger("click");
    await flushPromises();
    const note = w.get('[data-testid="model-set-suspect"]');
    expect(note.text()).toContain("Is Style a problem in this set?");
    expect(
      note
        .get('[data-testid="verdict-not_problem"]')
        .attributes("aria-pressed"),
    ).toBe("true");
    await note.get('[data-testid="verdict-cancel"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(document.activeElement).toBe(second.element);
    w.unmount();
  });

  it("returns focus to the line button when the member has its own suspect note", async () => {
    const w = mountPanel({
      check: entry("pass", {
        suspects: [suspect(3, { verdict: "problem" })],
        member_verdicts: [{ model_id: 3, verdict: "problem" }],
      }),
    });
    const button = lineButtons(w)[0];
    await button.trigger("click");
    await flushPromises();
    await w.get('[data-testid="verdict-cancel"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(document.activeElement).toBe(button.element);
    w.unmount();
  });
});
