// The Workflow tab's Also used list: where focus goes after an Add.
import { afterEach, describe, expect, it } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import WorkflowLoraPile from "./WorkflowLoraPile.vue";

const row = (asset, name) => ({ asset, sha256: asset, name, pictures: 3 });
const summary = (...rows) => ({ pictures: 9, varying: rows, without: null });
const ADA = row("ada", "Ada");
const BO = row("bo", "Bo");
const CY = row("cy", "Cy");

// A plain button carrying the attributes and clicks AppButton passes on.
const AppButtonStub = {
  template: '<button type="button"><slot /></button>',
};

const mounted = [];
afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
});

function mountPile() {
  const wrapper = mount(WorkflowLoraPile, {
    props: { summary: summary(ADA, BO, CY) },
    global: { stubs: { Tooltip: true, AppButton: AppButtonStub } },
    attachTo: document.body,
  });
  mounted.push(wrapper);
  return wrapper;
}

const addButton = (wrapper, asset) =>
  wrapper.find(`[data-testid="wftab-also-row-${asset}"] [data-testid="wftab-add-default"]`);

describe("WorkflowLoraPile focus after Add", () => {
  it("moves focus to the row that took the added one's place", async () => {
    const wrapper = mountPile();
    await addButton(wrapper, "bo").trigger("click");
    await wrapper.setProps({ adding: "bo" });
    await wrapper.setProps({ summary: summary(ADA, CY), adding: "" });
    await flushPromises();
    expect(document.activeElement.textContent).toContain("Show 3");
    expect(
      document.activeElement.closest("[data-testid='wftab-also-row-cy']"),
    ).not.toBeNull();
  });

  it("forgets a failed add, so a later change that takes that row out moves no focus", async () => {
    const wrapper = mountPile();
    await addButton(wrapper, "bo").trigger("click");
    // The add is out, then fails: the row stays.
    await wrapper.setProps({ adding: "bo" });
    await wrapper.setProps({ adding: "" });
    await flushPromises();
    const outside = document.createElement("button");
    document.body.append(outside);
    outside.focus();
    // Later, Bo leaves for another reason.
    await wrapper.setProps({ summary: summary(ADA, CY) });
    await flushPromises();
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });
});
