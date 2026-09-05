// The model shelf against a READ/share credential (issue #1014).
//
// Every route the shelf calls is owner-only, so the backend answers a share
// session with 403 on all of them. Nothing leaks - but the session was still
// offered the destination, and a direct `/models` URL mounted the whole screen
// so it could fire a burst of requests it could never satisfy. Two halves fix
// it, and this file pins the navigation half:
//
//   * `isModelsView` is false for a READ session, so `App.vue` never mounts
//     `ModelShelf` and no model request is issued at all;
//   * the session is bounced to the library, so it does not sit on a URL that
//     renders the picture grid.
//
// `sessionContext` here is the REAL one from `apiClient`, not a mocked
// `isReadOnly` ref, so the scoped and unscoped cases genuinely differ in what
// they feed the predicate rather than both being the same hand-set boolean.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

const nav = vi.hoisted(() => ({ route: null, replace: null, push: null }));
vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual("vue");
  const { vi: vitest } = await import("vitest");
  nav.route = reactive({
    path: "/",
    name: "all-pictures",
    params: {},
    query: {},
  });
  nav.replace = vitest.fn();
  nav.push = vitest.fn();
  return {
    useRoute: () => nav.route,
    useRouter: () => ({
      push: nav.push,
      replace: nav.replace,
      currentRoute: { value: nav.route },
    }),
  };
});

import { sessionContext } from "../utils/apiClient";
import { useAppNavigation } from "./useAppNavigation";

/** Mount a bare component whose only job is to hold the composable. */
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

const UNSCOPED_READ = { scope: "READ" };
const SCOPED_READ = {
  scope: "READ",
  resource_type: "picture_set",
  resource_id: 12,
};

// A share session's credential lives in the query string, so every session that
// can reach the bounce has one, and the bounce has to carry it forward.
const SHARE_TOKEN = "example-share-token";

/** Where the bounce must land, credential intact. */
const BOUNCE_TARGET = {
  name: "all-pictures",
  query: { token: SHARE_TOKEN },
};

beforeEach(() => {
  setActivePinia(createPinia());
  sessionContext.value = null;
  nav.route.name = "all-pictures";
  nav.route.path = "/";
  nav.route.query = {};
  nav.replace.mockReset().mockReturnValue(Promise.resolve());
  nav.push.mockReset().mockReturnValue(Promise.resolve());
});

describe("the model shelf and a READ session", () => {
  for (const [label, ctx] of [
    ["an unscoped READ credential", UNSCOPED_READ],
    ["a scoped READ credential", SCOPED_READ],
  ]) {
    it(`does not show the shelf to ${label} that lands on /models`, () => {
      sessionContext.value = ctx;
      nav.route.name = "models";
      nav.route.query = { token: SHARE_TOKEN };
      const { wrapper, api } = mountNav();

      expect(api.isModelsView.value).toBe(false);
      expect(nav.replace).toHaveBeenCalledWith(BOUNCE_TARGET);

      wrapper.unmount();
    });

    it(`does not show the runs tab to ${label} either`, () => {
      sessionContext.value = ctx;
      nav.route.name = "models-runs";
      nav.route.query = { token: SHARE_TOKEN };
      const { wrapper, api } = mountNav();

      expect(api.isModelsView.value).toBe(false);
      expect(nav.replace).toHaveBeenCalledWith(BOUNCE_TARGET);

      wrapper.unmount();
    });
  }

  it("bounces a READ session that navigates to /models after mount", async () => {
    // The share session is already in the app when the URL changes - a pasted
    // link, a Back into a models entry left in history.
    sessionContext.value = UNSCOPED_READ;
    nav.route.query = { token: SHARE_TOKEN };
    const { wrapper, api } = mountNav();
    expect(nav.replace).not.toHaveBeenCalled();

    nav.route.name = "models";
    await wrapper.vm.$nextTick();

    expect(api.isModelsView.value).toBe(false);
    expect(nav.replace).toHaveBeenCalledWith(BOUNCE_TARGET);

    wrapper.unmount();
  });

  // The bounce is the ONLY navigation in the app that fires exclusively for a
  // share session, so a dropped `?token=` here would break the share link for
  // every visitor it fires for and nobody else - invisible until the next
  // reload, when `Root.vue` finds no token and shows the login screen.
  it("carries the share token through the bounce", () => {
    sessionContext.value = SCOPED_READ;
    nav.route.name = "models";
    nav.route.query = { token: SHARE_TOKEN };
    const { wrapper } = mountNav();

    const target = nav.replace.mock.calls[0][0];
    expect(target.query?.token).toBe(SHARE_TOKEN);

    wrapper.unmount();
  });

  it("leaves a READ session alone on the routes it may use", () => {
    sessionContext.value = SCOPED_READ;
    nav.route.name = "set";
    const { wrapper } = mountNav();

    expect(nav.replace).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  // The positive control: over-blocking is its own regression, and a guard that
  // reads `isReadOnly` inverted would still pass every assertion above.
  it("still shows the owner both of the shelf's routes", async () => {
    nav.route.name = "models";
    const { wrapper, api } = mountNav();
    expect(api.isModelsView.value).toBe(true);

    nav.route.name = "models-runs";
    await wrapper.vm.$nextTick();
    expect(api.isModelsView.value).toBe(true);
    expect(nav.replace).not.toHaveBeenCalled();

    wrapper.unmount();
  });
});
