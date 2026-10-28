// Favourites is All media with `favourite=1` on (#2155), a place beside
// Scrapheap. Going from Favourites to Scrapheap must drop the filter, or the
// heap opens filtered to its hearted pictures with Favourites' chip still on.

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
import { useFilterStore } from "../stores/useFilterStore";

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

beforeEach(() => {
  setActivePinia(createPinia());
  nav.push.mockReset().mockReturnValue(Promise.resolve());
  nav.replace.mockReset().mockReturnValue(Promise.resolve());
});

describe("leaving Favourites", () => {
  it("for Scrapheap drops the Favourites filter", async () => {
    const { wrapper, api } = mountNav();
    const filters = useFilterStore();
    filters.favouriteOnlyFilter = true;
    await api.handleSelectCharacter({ id: "SCRAPHEAP", label: "Scrapheap" });
    expect(filters.favouriteOnlyFilter).toBe(false);
    wrapper.unmount();
  });

  it("for a person keeps it, as the filter it also is", async () => {
    const { wrapper, api } = mountNav();
    const filters = useFilterStore();
    filters.favouriteOnlyFilter = true;
    await api.handleSelectCharacter({ id: 7, ids: [7] });
    expect(filters.favouriteOnlyFilter).toBe(true);
    wrapper.unmount();
  });
});
