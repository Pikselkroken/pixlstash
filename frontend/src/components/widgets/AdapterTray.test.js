// The read-only adapter tray in the person and set editors.
//
// The assertions worth having are about the states that LOOK alike and are not:
// an entity with adapters, an entity with none, an entity that does not exist
// yet, a caller the owner-only shelf refuses, and a read that simply failed.
// Only the second may say "none" - every one of the others saying it would be a
// claim about the entity drawn from something that is not about the entity.
//
// Several of these assert what LANDS rather than what was requested. A test
// that only checks the outgoing call passes while the response handling is
// broken, which is exactly how the stale-response race below survives a suite
// that does exercise the id-change path.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

const listAdapters = vi.fn();
vi.mock("../../api/modelShelf", () => ({
  listAdapters: (...args) => listAdapters(...args),
}));
vi.mock("../../api/modelIcons", () => ({
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));
import AdapterTray from "./AdapterTray.vue";

function adapter(overrides = {}) {
  return {
    id: 1,
    sha256: "a".repeat(64),
    display_name: "Cyanwood Style",
    filename: "Cyanwood_Style_000000250.safetensors",
    base_model: "flux.1-dev",
    trigger_words: null,
    icon_sha256: null,
    added_at: "2026-08-01T10:00:00Z",
    ...overrides,
  };
}

/** The default double: adapters on the first call, no unknowns on the second. */
function serveAdapters(rows) {
  listAdapters.mockImplementation(({ fileKind }) =>
    Promise.resolve(fileKind === "adapter" ? rows : []),
  );
}

async function mountTray(props = {}) {
  const wrapper = mount(AdapterTray, {
    props: { entityType: "character", entityId: 7, ...props },
  });
  await flushPromises();
  return wrapper;
}

const names = (wrapper) =>
  wrapper.findAll(".adapter-card__name").map((n) => n.text());

beforeEach(() => {
  listAdapters.mockReset();
  serveAdapters([]);
});

describe("AdapterTray", () => {
  it("filters server-side by the entity it was given", async () => {
    // Both kinds asserted per mount, and as the WHOLE call list: a
    // `toHaveBeenCalledWith` per kind would pass while the other never fired.
    // Compared as a set, so reordering `ATTACHABLE_FILE_KINDS` is not a failure.
    await mountTray({ entityType: "character", entityId: 7 });
    expect(listAdapters.mock.calls.flat()).toEqual(
      expect.arrayContaining([
        { characterId: 7, fileKind: "adapter" },
        { characterId: 7, fileKind: "unknown" },
      ]),
    );
    expect(listAdapters).toHaveBeenCalledTimes(2);

    listAdapters.mockClear();
    await mountTray({ entityType: "set", entityId: 12 });
    expect(listAdapters.mock.calls.flat()).toEqual(
      expect.arrayContaining([
        { setId: 12, fileKind: "adapter" },
        { setId: 12, fileKind: "unknown" },
      ]),
    );
    expect(listAdapters).toHaveBeenCalledTimes(2);
  });

  it("shows an attached UNCLASSIFIED file, not just a classified adapter", async () => {
    // Of the file kinds, the attach route rejects only a checkpoint (400) and
    // an engine (409), so an `unknown` row can carry an attachment. Asking for
    // `file_kind=adapter` alone told the owner "no adapters yet" about a person
    // whose shelf row was showing their mark.
    listAdapters.mockImplementation(({ fileKind }) =>
      Promise.resolve(
        fileKind === "unknown"
          ? [
              adapter({
                id: 2,
                display_name: "Unfiled Thing",
                added_at: "2026-08-02T10:00:00Z",
              }),
            ]
          : [],
      ),
    );
    const wrapper = await mountTray();
    expect(names(wrapper)).toEqual(["Unfiled Thing"]);
  });

  it("draws a card per adapter, newest first across both kinds", async () => {
    listAdapters.mockImplementation(({ fileKind }) =>
      Promise.resolve(
        fileKind === "adapter"
          ? [adapter()]
          : [
              adapter({
                id: 2,
                sha256: "b".repeat(64),
                display_name: null,
                filename: "ivy.safetensors",
                base_model: null,
                trigger_words: "ivy_woman",
                added_at: "2026-08-05T10:00:00Z",
              }),
            ],
      ),
    );
    const wrapper = await mountTray();
    const cards = wrapper.findAll(".adapter-card");
    expect(cards).toHaveLength(2);
    // The newer unknown sorts above the older adapter, so the merge is not just
    // one list appended to the other.
    expect(cards[0].find(".adapter-card__name").text()).toBe("ivy");
    expect(cards[1].find(".adapter-card__meta").text()).toBe("flux.1-dev");
    // A missing base model is rendered, not dropped - a blank cell is the
    // failure mode the shelf's naming rules exist to avoid.
    expect(cards[0].find(".adapter-card__meta").text()).toBe(
      "Base model not set",
    );
    expect(cards[0].find(".adapter-card__trigger").text()).toBe("ivy_woman");
  });

  it("names a row that has nothing to be named from", async () => {
    // `modelName`'s last state, and the only one that returns "": no title and
    // no filename either. Deliberate on the shelf, where an empty field invites
    // the rename it is asking for; here it would be a card with a hole in it.
    serveAdapters([adapter({ display_name: null, filename: null })]);
    expect(names(await mountTray())).toEqual(["Unnamed adapter"]);
  });

  it("still shows the raw filename when only the title is missing", async () => {
    // `000002750.safetensors` is all training bookkeeping, so nothing survives
    // the strip and the chain falls back to the file's own string. The card
    // must not swallow that into "Unnamed adapter".
    serveAdapters([
      adapter({ display_name: null, filename: "000002750.safetensors" }),
    ]);
    expect(names(await mountTray())).toEqual(["000002750.safetensors"]);
  });

  it("says so when the entity uses none", async () => {
    const wrapper = await mountTray();
    expect(wrapper.find(".adapter-tray").exists()).toBe(true);
    expect(wrapper.find(".adapter-tray__empty").exists()).toBe(true);
  });

  it("draws nothing at all, heading included, until the first read settles", async () => {
    // The empty line is a statement about the entity, and the heading is a
    // promise there is something under it; before the answer is back neither is
    // earned. A bare heading over empty space that is then taken away again is
    // what a refused session would get on every single open. Mounted without
    // flushing on purpose.
    listAdapters.mockImplementation(() => new Promise(() => {}));
    const wrapper = mount(AdapterTray, {
      props: { entityType: "character", entityId: 7 },
    });
    expect(wrapper.find(".adapter-tray").exists()).toBe(false);
  });

  it("stays out of the way before the entity has an id", async () => {
    for (const entityId of [null, undefined, ""]) {
      const wrapper = await mountTray({ entityId });
      expect(wrapper.find(".adapter-tray").exists()).toBe(false);
    }
    // `Number(null)` and `Number("")` are both 0, so a conversion done before
    // the emptiness check would have filtered the shelf by entity 0.
    expect(listAdapters).not.toHaveBeenCalled();
  });

  it("takes an id given as a string", async () => {
    // `character.id` arrives from the API as a number, but the prop admits a
    // string and a `v-for` key or a route param is where one comes from.
    serveAdapters([adapter()]);
    const wrapper = await mountTray({ entityId: "7" });
    expect(listAdapters).toHaveBeenCalledWith({
      characterId: 7,
      fileKind: "adapter",
    });
    expect(wrapper.findAll(".adapter-card")).toHaveLength(1);
  });

  it("hides itself rather than claiming 'none' when the shelf refuses", async () => {
    // `GET /adapters` is owner-only. "No adapters yet" from a token that may
    // not read the shelf would be a statement about the entity made from an
    // answer about the caller.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockRejectedValue({ response: { status: 403 } });
    const wrapper = await mountTray();
    expect(wrapper.find(".adapter-tray").exists()).toBe(false);
    // A refusal is an expected state for a scoped session, not a defect.
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("reports any OTHER failure instead of vanishing", async () => {
    // A vanished section reads as "no adapters" too, so a 500 or a dropped
    // connection has to say something. CLAUDE.md: no silent failures.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockRejectedValue({ response: { status: 500 } });
    const wrapper = await mountTray();
    expect(wrapper.find(".adapter-tray").exists()).toBe(true);
    expect(wrapper.find(".adapter-tray__empty").exists()).toBe(false);
    expect(wrapper.find(".adapter-tray__error").text()).toBeTruthy();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("clears an earlier failure once a read succeeds", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockRejectedValue({ response: { status: 500 } });
    const wrapper = await mountTray({ entityId: 7 });
    expect(wrapper.find(".adapter-tray__error").exists()).toBe(true);

    serveAdapters([adapter()]);
    await wrapper.setProps({ entityId: 8 });
    await flushPromises();
    expect(wrapper.find(".adapter-tray__error").exists()).toBe(false);
    expect(names(wrapper)).toEqual(["Cyanwood Style"]);
    spy.mockRestore();
  });

  it("never paints one entity's adapters into another entity's editor", async () => {
    // The read for entity 7 is left in flight and answered AFTER entity 8's,
    // which is the ordinary ordering when the first request is the slow one.
    // Without a sequence guard the stale answer lands last and wins.
    // BOTH of entity 7's flights are held, one per file kind: releasing only
    // one leaves its `Promise.all` pending for ever, and the test would then
    // pass with no sequence guard at all.
    const heldSeven = [];
    listAdapters.mockImplementation(({ characterId, fileKind }) => {
      if (characterId === 7) {
        return new Promise((resolve) =>
          heldSeven.push(() =>
            resolve(
              fileKind === "adapter"
                ? [adapter({ display_name: "Seven" })]
                : [],
            ),
          ),
        );
      }
      return Promise.resolve(
        fileKind === "adapter"
          ? [adapter({ id: 2, display_name: "Eight" })]
          : [],
      );
    });
    const releaseSeven = () => heldSeven.forEach((release) => release());

    const wrapper = mount(AdapterTray, {
      props: { entityType: "character", entityId: 7 },
    });
    await wrapper.setProps({ entityId: 8 });
    await flushPromises();
    releaseSeven();
    await flushPromises();

    expect(names(wrapper)).toEqual(["Eight"]);
  });

  it("drops the old entity's cards the moment it is pointed at a new one", async () => {
    // The sequence guard alone only stops a late answer from WINNING. The rows
    // already on screen belong to the previous person, and without clearing
    // them before the await they sit under the new person's name - with a
    // confident "1 attached" over them - for as long as the read takes.
    const heldEight = [];
    listAdapters.mockImplementation(({ characterId, fileKind }) => {
      if (characterId === 7) {
        return Promise.resolve(
          fileKind === "adapter" ? [adapter({ display_name: "Seven" })] : [],
        );
      }
      return new Promise((resolve) => heldEight.push(() => resolve([])));
    });

    const wrapper = await mountTray({ entityId: 7 });
    expect(names(wrapper)).toEqual(["Seven"]);

    await wrapper.setProps({ entityId: 8 });
    await flushPromises();
    // Entity 8's read has NOT answered yet. The section stays - it earned its
    // place on the first read and tearing it down every time would be the
    // appear-and-vanish the `settled` gate exists to stop - but it claims
    // nothing: no cards, no count, and NOT the "no adapters yet" line, which
    // would be a statement about entity 8 made before entity 8 answered.
    expect(wrapper.find(".adapter-tray").exists()).toBe(true);
    expect(wrapper.find(".adapter-card").exists()).toBe(false);
    expect(wrapper.find(".adapter-tray__count").exists()).toBe(false);
    expect(wrapper.find(".adapter-tray__empty").exists()).toBe(false);
  });

  it("does not let a read land after the entity loses its id", async () => {
    // Saving is not the only way an editor's entity goes away, and a late
    // answer arriving after it did would repopulate a tray for nothing.
    const held = [];
    listAdapters.mockImplementation(
      ({ fileKind }) =>
        new Promise((resolve) =>
          held.push(() => resolve(fileKind === "adapter" ? [adapter()] : [])),
        ),
    );
    const wrapper = await mountTray({ entityId: 7 });
    await wrapper.setProps({ entityId: null });
    held.forEach((release) => release());
    await flushPromises();
    expect(wrapper.find(".adapter-tray").exists()).toBe(false);
  });

  it("keeps the rows one kind returned when the other kind fails, and says the list is short", async () => {
    // `Promise.all` would throw away real adapters because the unknown query
    // broke. The rows that arrived are true AND the list is short of what the
    // entity has: both go on screen, because showing only the first is a
    // confident, wrong answer.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockImplementation(({ fileKind }) =>
      fileKind === "adapter"
        ? Promise.resolve([adapter()])
        : Promise.reject({ response: { status: 500 } }),
    );
    const wrapper = await mountTray();
    expect(names(wrapper)).toEqual(["Cyanwood Style"]);
    expect(wrapper.find(".adapter-tray__error").text()).toContain(
      "may be short",
    );
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("never prints 'no adapters yet' when a read failed and the rest was empty", async () => {
    // The reproduced defect: the surviving kind legitimately returns nothing,
    // the other kind 500s, and the tray told the owner their person uses no
    // adapters - while the shelf was showing that person's mark on three rows.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockImplementation(({ fileKind }) =>
      fileKind === "adapter"
        ? Promise.resolve([])
        : Promise.reject({ response: { status: 500 } }),
    );
    const wrapper = await mountTray();
    expect(wrapper.find(".adapter-tray__empty").exists()).toBe(false);
    expect(wrapper.find(".adapter-tray__error").exists()).toBe(true);
    spy.mockRestore();
  });

  it("reports the fault, not whichever refusal came first in the array", async () => {
    // One kind 403s and the other 500s. The 403 carries no `detail`, so picking
    // it by position costs the reader the one sentence that said what broke -
    // and which they got would be decided by the order of a constant.
    //
    // Asserted on the TEXT, not on the error line existing: both orderings show
    // an error either way, and only the text says which reason survived.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    for (const faultKind of ["adapter", "unknown"]) {
      listAdapters.mockImplementation(({ fileKind }) =>
        Promise.reject(
          fileKind === faultKind
            ? { response: { status: 500, data: { detail: "hub is offline" } } }
            : { response: { status: 403 } },
        ),
      );
      const wrapper = await mountTray();
      expect(wrapper.find(".adapter-tray__error").text()).toBe(
        "Couldn't read the adapters. hub is offline",
      );
    }
    spy.mockRestore();
  });

  it("does not read a partial refusal as permission to say 'none'", async () => {
    // The four-cell matrix's missing corner. One flight is refused and the
    // other succeeds with nothing: `total` is false so the refused branch is
    // skipped, and a fault filtered to non-403s is undefined so the error
    // branch was skipped too - and the tray printed "No adapters yet".
    //
    // A refusal beside a SUCCESS is not a session that may not read the shelf;
    // it is a session that changed underneath two concurrent requests. That is
    // a fault to report, not a permission to respect.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockImplementation(({ fileKind }) =>
      fileKind === "adapter"
        ? Promise.resolve([])
        : Promise.reject({ response: { status: 403 } }),
    );
    const wrapper = await mountTray();
    expect(wrapper.find(".adapter-tray").exists()).toBe(true);
    expect(wrapper.find(".adapter-tray__empty").exists()).toBe(false);
    expect(wrapper.find(".adapter-tray__error").text()).toContain(
      "may be short",
    );
    spy.mockRestore();
  });

  it("withholds the count when it knows the list is short", async () => {
    // "1 attached" over a list a failure already truncated is the confident
    // wrong number this component is built not to print. The error line says
    // the list is short; a figure standing beside it reads as the total anyway.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockImplementation(({ fileKind }) =>
      fileKind === "adapter"
        ? Promise.resolve([adapter()])
        : Promise.reject({ response: { status: 500 } }),
    );
    const wrapper = await mountTray();
    expect(wrapper.findAll(".adapter-card")).toHaveLength(1);
    expect(wrapper.find(".adapter-tray__count").exists()).toBe(false);
    spy.mockRestore();
  });

  it("does not hide a partial refusal behind the rows that did arrive", async () => {
    // The same corner with the surviving kind non-empty: one card and no
    // indication the list is short is a silent failure, which CLAUDE.md forbids
    // outright - and it is the shape a reader is least likely to question.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockImplementation(({ fileKind }) =>
      fileKind === "adapter"
        ? Promise.resolve([adapter()])
        : Promise.reject({ response: { status: 403 } }),
    );
    const wrapper = await mountTray();
    expect(names(wrapper)).toEqual(["Cyanwood Style"]);
    expect(wrapper.find(".adapter-tray__error").exists()).toBe(true);
    spy.mockRestore();
  });

  it("keeps a refused section hidden across a re-read instead of blinking it", async () => {
    // `refused` is what this session was told about the shelf, not a claim
    // about the entity. Clearing it before each read walked the tray back
    // through visible - a bare heading over empty space, then gone again, on
    // every open - which is the appear-and-vanish the gate exists to stop.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockRejectedValue({ response: { status: 403 } });
    const wrapper = await mountTray({ entityId: 7 });
    expect(wrapper.find(".adapter-tray").exists()).toBe(false);

    await wrapper.setProps({ entityId: 8 });
    // Mid-read, before entity 8 has answered.
    expect(wrapper.find(".adapter-tray").exists()).toBe(false);
    await flushPromises();
    expect(wrapper.find(".adapter-tray").exists()).toBe(false);
    spy.mockRestore();
  });

  it("comes back when a later entity is allowed after a refused one", async () => {
    // The other direction: `refused` must not be sticky either, or one refusal
    // would silence the tray for the rest of the session.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockRejectedValue({ response: { status: 403 } });
    const wrapper = await mountTray({ entityId: 7 });
    expect(wrapper.find(".adapter-tray").exists()).toBe(false);

    serveAdapters([adapter()]);
    await wrapper.setProps({ entityId: 8 });
    await flushPromises();
    expect(names(wrapper)).toEqual(["Cyanwood Style"]);
    spy.mockRestore();
  });

  it("counts and names what it is showing", async () => {
    serveAdapters([adapter(), adapter({ id: 2, display_name: "Second" })]);
    const wrapper = await mountTray();
    expect(wrapper.find(".adapter-tray__count").text()).toBe("2 attached");
    // The list is named through the heading; "list, 2 items" on its own says
    // nothing about what of.
    const heading = wrapper.find(".section-label");
    expect(wrapper.find("ul").attributes("aria-labelledby")).toBe(
      heading.attributes("id"),
    );
    expect(heading.attributes("id")).toBeTruthy();
  });

  it("carries the name AND the file it came from in the hover text", async () => {
    // The name is one ellipsised line in a narrow track, so the recovery has to
    // include it; a bare `title="filename"` gave back the one thing that was
    // not truncated. A derived name still shows its file, because the file
    // keeps what the derivation dropped (the extension, the training suffix).
    // Only an exact repeat is suppressed: `modelName`'s `from-file` state IS
    // the filename, and saying it twice with a dash between is not more.
    serveAdapters([
      adapter({
        display_name: "Cyanwood Style",
        filename: "cw_v3.safetensors",
      }),
      adapter({ id: 2, display_name: null, filename: "ivy.safetensors" }),
      adapter({ id: 3, display_name: null, filename: "000002750.safetensors" }),
    ]);
    const cards = (await mountTray()).findAll(".adapter-card");
    expect(cards[0].attributes("title")).toBe(
      "Cyanwood Style - cw_v3.safetensors",
    );
    expect(cards[1].attributes("title")).toBe("ivy - ivy.safetensors");
    expect(cards[2].attributes("title")).toBe("000002750.safetensors");
  });

  it("asks for nothing at all if it is not told which kind of entity", async () => {
    // A ternary on `entityType` makes every value that is not the one it names
    // mean "set", so a typo renders a picture set's adapters under a person's
    // name. The lookup fails closed instead: no filter, no request, no claim.
    //
    // `constructor` is in the list because `in` walks the prototype chain: it
    // passes an `entityType in FILTER_KEY` guard, resolves to a function, and
    // spreads into the request as a key no route declares - which FastAPI drops,
    // so the "filtered" read answers with every adapter on the machine.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    for (const entityType of ["characters", "constructor", "toString", ""]) {
      const wrapper = await mountTray({ entityType, entityId: 7 });
      expect(wrapper.find(".adapter-tray").exists()).toBe(false);
    }
    expect(listAdapters).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("stops claiming anything if the entity type goes invalid after a read", async () => {
    // `settled` is one-way, so without the template gating on the type too, the
    // section stays up with its rows cleared and says "No adapters yet" about
    // an entity it can no longer address.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    serveAdapters([adapter()]);
    const wrapper = await mountTray({ entityType: "character", entityId: 7 });
    expect(wrapper.find(".adapter-card").exists()).toBe(true);

    await wrapper.setProps({ entityType: "characters" });
    await flushPromises();
    expect(wrapper.find(".adapter-tray").exists()).toBe(false);
    warn.mockRestore();
  });

  it("gives each instance its own heading id", async () => {
    // Both editors can be mounted at once, and two lists pointing
    // `aria-labelledby` at one id name the same heading for different content.
    // Mounted inside ONE parent on purpose: `useId` counts per app, so two
    // separate `mount()` calls would collide here and never in the real app.
    serveAdapters([adapter()]);
    const wrapper = mount(
      {
        components: { AdapterTray },
        template: `<div>
          <AdapterTray entity-type="character" :entity-id="7" />
          <AdapterTray entity-type="set" :entity-id="7" />
        </div>`,
      },
      {},
    );
    await flushPromises();
    const ids = wrapper
      .findAll("ul")
      .map((ul) => ul.attributes("aria-labelledby"));
    expect(ids).toHaveLength(2);
    expect(ids[0]).toBeTruthy();
    expect(new Set(ids).size).toBe(2);
  });

  it("renders a set's adapters, not just a character's", async () => {
    serveAdapters([adapter()]);
    const wrapper = await mountTray({ entityType: "set", entityId: 12 });
    expect(names(wrapper)).toEqual(["Cyanwood Style"]);
  });

  it("re-reads when only the entity TYPE changes", async () => {
    // Id 7 the character and id 7 the set are different entities. Watching the
    // id alone would keep showing the first one's adapters.
    const wrapper = await mountTray({ entityType: "character", entityId: 7 });
    listAdapters.mockClear();
    await wrapper.setProps({ entityType: "set" });
    await flushPromises();
    expect(listAdapters.mock.calls.flat()).toEqual(
      expect.arrayContaining([
        { setId: 7, fileKind: "adapter" },
        { setId: 7, fileKind: "unknown" },
      ]),
    );
  });

  it("dates a stack by its newest member, as the shelf does", async () => {
    // A six-step run registered in January whose newest file landed yesterday
    // is a recent thing. Ordering it by the cover's `added_at` puts it at the
    // bottom here and the top of the shelf - one relation, two orders, off one
    // payload.
    serveAdapters([
      adapter({
        id: 1,
        display_name: "Lone",
        added_at: "2026-07-01T00:00:00Z",
      }),
      adapter({
        id: 2,
        display_name: "Stack",
        added_at: "2026-01-01T00:00:00Z",
        newest_member_at: "2026-08-10T00:00:00Z",
      }),
    ]);
    expect(names(await mountTray())).toEqual(["Stack", "Lone"]);
  });

  it("puts a row with no date last, not first", async () => {
    // `Optional[str]` on the wire. Sorting descending on a bare null would put
    // the row it knows least about at the top.
    serveAdapters([
      adapter({ id: 1, display_name: "Undated", added_at: null }),
      adapter({
        id: 2,
        display_name: "Dated",
        added_at: "2020-01-01T00:00:00Z",
      }),
    ]);
    expect(names(await mountTray())).toEqual(["Dated", "Undated"]);
  });

  it("still says something when the failure carries no server reason", async () => {
    // A dead backend or a dropped connection rejects with no `response` at all,
    // so `errorDetail` is "" and the reader would get a bare "Couldn't read the
    // adapters." with nothing to act on.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockRejectedValue(new Error("Network Error"));
    const wrapper = await mountTray();
    expect(wrapper.find(".adapter-tray__error").text()).toBe(
      "Couldn't read the adapters. Network Error",
    );
    spy.mockRestore();
  });

  it("announces the failure rather than only drawing it", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockRejectedValue({ response: { status: 500 } });
    const wrapper = await mountTray();
    expect(wrapper.find(".adapter-tray__error").attributes("role")).toBe(
      "alert",
    );
    spy.mockRestore();
  });

  it("does not report a failure against an entity it has stopped pointing at", async () => {
    // The read is still in flight when the id goes away. Letting it land would
    // log - and count - a failure for an entity nobody is looking at.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const held = [];
    listAdapters.mockImplementation(
      () =>
        new Promise((_resolve, reject) =>
          held.push(() => reject({ response: { status: 500 } })),
        ),
    );
    const wrapper = mount(AdapterTray, {
      props: { entityType: "character", entityId: 7 },
    });
    await wrapper.setProps({ entityId: null });
    held.forEach((release) => release());
    await flushPromises();
    expect(spy).not.toHaveBeenCalled();
    expect(wrapper.find(".adapter-tray").exists()).toBe(false);
    spy.mockRestore();
  });

  it("leads the failure line with what failed, then the server's reason", async () => {
    // Whether this is ALL of the adapters or some of them is the part the
    // reader needs, and no server detail says it - so our sentence comes first
    // and the server's is appended.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    listAdapters.mockRejectedValue({
      response: { status: 500, data: { detail: "shelf index is rebuilding" } },
    });
    const wrapper = await mountTray();
    expect(wrapper.find(".adapter-tray__error").text()).toBe(
      "Couldn't read the adapters. shelf index is rebuilding",
    );
    spy.mockRestore();
  });

  it("makes no request for an id that is not a whole number", async () => {
    // `7.5` is finite, so a `Number.isFinite` guard passes it straight to an
    // `Optional[int]` Query and the reader gets a 422 to interpret. These are
    // row ids; anything that is not one of those is not an id.
    for (const entityId of ["not-an-id", 7.5, NaN, Infinity]) {
      const wrapper = await mountTray({ entityId });
      expect(wrapper.find(".adapter-tray").exists()).toBe(false);
    }
    expect(listAdapters).not.toHaveBeenCalled();
  });

  it("re-reads when it is pointed at a different entity", async () => {
    const wrapper = await mountTray({ entityId: 7 });
    listAdapters.mockClear();
    await wrapper.setProps({ entityId: 8 });
    await flushPromises();
    expect(listAdapters.mock.calls.flat()).toEqual(
      expect.arrayContaining([
        { characterId: 8, fileKind: "adapter" },
        { characterId: 8, fileKind: "unknown" },
      ]),
    );
  });
});
