// useScopedNotice - a notice family dies with the context it describes.
//
// The case that matters is the ordering one: the operation that pushes these
// notices (the bulk delete) mutates the selection they are scoped to, and an
// eagerly-armed watcher would dismiss the report on the pusher's own mutation.
// Two of the tests below exist purely to pin that.

import { describe, it, expect, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { ref, nextTick } from "vue";

import { useScopedNotice } from "./useScopedNotice";
import { useNoticeStore } from "../stores/useNoticeStore";

const KEYS = ["locked-card", "locked-help"];

let store;
let selection;
let view;

/** The signature the composable watches: selection + which view it is in. */
const signature = () => `${selection.value.join(",")}|${view.value}`;

/** Push the pair the real caller pushes: a sticky card plus its follow-up. */
function pushFamily() {
  store.warning("3 selected pictures are in locked sets.", {
    key: "locked-card",
    action: { label: "Help", handler: () => {} },
  });
  store.push({
    level: "info",
    text: "Unlock the set to delete them.",
    timeout: 0,
    key: "locked-help",
  });
}

const liveKeys = () => store.notices.map((n) => n.key).sort();

beforeEach(() => {
  setActivePinia(createPinia());
  store = useNoticeStore();
  selection = ref([1, 2, 3]);
  view = ref("character:7");
});

describe("useScopedNotice", () => {
  it("survives the mutation that produced it", async () => {
    const scoped = useScopedNotice(KEYS, signature);

    // Exactly the real sequence: the delete narrows the selection to the frozen
    // survivors, THEN reports what it skipped.
    selection.value = [3];
    pushFamily();
    scoped.arm();

    await nextTick();
    await nextTick();
    expect(liveKeys()).toEqual(["locked-card", "locked-help"]);
  });

  it("dismisses the whole family on the next context change", async () => {
    const scoped = useScopedNotice(KEYS, signature);
    pushFamily();
    scoped.arm();
    await nextTick();
    await nextTick();

    selection.value = [9];
    await nextTick();
    expect(store.notices).toEqual([]);
  });

  it("dismisses when the view changes with the selection intact", async () => {
    const scoped = useScopedNotice(KEYS, signature);
    pushFamily();
    scoped.arm();
    await nextTick();
    await nextTick();

    view.value = "set:2";
    await nextTick();
    expect(store.notices).toEqual([]);
  });

  it("leaves unrelated notices alone", async () => {
    const scoped = useScopedNotice(KEYS, signature);
    pushFamily();
    store.error("Something else failed.", { key: "other" });
    scoped.arm();
    await nextTick();
    await nextTick();

    selection.value = [9];
    await nextTick();
    expect(liveKeys()).toEqual(["other"]);
  });

  it("does nothing when the context changes with nothing armed", async () => {
    useScopedNotice(KEYS, signature);
    store.error("Unrelated.", { key: "other" });

    selection.value = [9];
    await nextTick();
    expect(liveKeys()).toEqual(["other"]);
  });

  it("re-arms on a repeat so the second card gets its own window", async () => {
    const scoped = useScopedNotice(KEYS, signature);
    pushFamily();
    scoped.arm();
    await nextTick();
    await nextTick();

    // A second locked delete against a different selection: coalescing updates
    // the card, and re-arming rebaselines to the selection it now describes.
    selection.value = [4, 5];
    pushFamily();
    scoped.arm();
    await nextTick();
    await nextTick();
    expect(liveKeys()).toEqual(["locked-card", "locked-help"]);

    selection.value = [6];
    await nextTick();
    expect(store.notices).toEqual([]);
  });

  it("invalidate() retires the family by hand", async () => {
    const scoped = useScopedNotice(KEYS, signature);
    pushFamily();
    scoped.arm();
    await nextTick();
    await nextTick();

    scoped.invalidate();
    expect(store.notices).toEqual([]);
  });
});
