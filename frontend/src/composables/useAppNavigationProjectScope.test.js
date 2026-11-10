// "Not in a project" in the URL (#2159). The other scopes have routes of their
// own (`/` and `/project/:id`); this one rides as `?project=none` on the
// global routes, so a reload or Back lands in the same scope rather than
// silently widening the grid to the whole library.
// `useViewStore.test.js` pins the read half.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

const nav = vi.hoisted(() => ({ route: null, push: null, replace: null }));
vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual("vue");
  const { vi: vitest } = await import("vitest");
  nav.route = reactive({
    path: "/",
    name: "all-pictures",
    params: {},
    query: {},
  });
  nav.push = vitest.fn();
  nav.replace = vitest.fn();
  return {
    useRoute: () => nav.route,
    useRouter: () => ({
      push: nav.push,
      replace: nav.replace,
      currentRoute: { value: nav.route },
    }),
  };
});

import { useAppNavigation } from "./useAppNavigation";

function mountNav() {
  let api = null;
  const wrapper = mount(
    defineComponent({
      setup() {
        api = useAppNavigation();
        return () => h("div");
      },
    }),
  );
  return { wrapper, api };
}

const NONE = { mode: "project", projectId: null };

beforeEach(() => {
  setActivePinia(createPinia());
  nav.push.mockReset().mockReturnValue(Promise.resolve());
  nav.replace.mockReset().mockReturnValue(Promise.resolve());
});

describe("the Not in a project scope in the URL", () => {
  it("marks All media", async () => {
    const { wrapper, api } = mountNav();
    await api.handleSelectCharacter({ id: "ALL", projectContext: NONE });
    expect(nav.push.mock.calls.at(-1)[0]).toMatchObject({
      name: "all-pictures",
      query: { project: "none" },
    });
    wrapper.unmount();
  });

  it("marks a person and a set", async () => {
    const { wrapper, api } = mountNav();
    await api.handleSelectCharacter({ id: 7, ids: [7], projectContext: NONE });
    expect(nav.push.mock.calls.at(-1)[0]).toMatchObject({
      name: "character",
      query: { project: "none" },
    });
    await api.handleSelectSet({ id: 3, ids: [3], projectContext: NONE });
    expect(nav.push.mock.calls.at(-1)[0]).toMatchObject({
      name: "set",
      query: { project: "none" },
    });
    wrapper.unmount();
  });

  it("leaves Any project and a real project unmarked", async () => {
    const { wrapper, api } = mountNav();
    await api.handleSelectCharacter({
      id: "ALL",
      projectContext: { mode: "global", projectId: null },
    });
    expect(nav.push.mock.calls.at(-1)[0].query ?? {}).toEqual({});
    await api.handleSelectCharacter({
      id: "ALL",
      projectContext: { mode: "project", projectId: 4 },
    });
    expect(nav.push.mock.calls.at(-1)[0]).toMatchObject({
      name: "project",
      params: { id: "4" },
    });
    wrapper.unmount();
  });
});
