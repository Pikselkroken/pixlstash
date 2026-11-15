// People › a character › Sheets: drawing, keeping and cutting character
// sheets, with every draw stored on the character so the screen comes back to
// it. The draws API is a small in-memory server below, which settles a run
// from `db.outcomes` the way the backend reads a run's ending into its draw.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { useTasksStore } from "../../stores/useTasksStore";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const router = vi.hoisted(() => ({ push: vi.fn(), back: vi.fn() }));
vi.mock("vue-router", () => ({ useRouter: () => router }));

const gen = vi.hoisted(() => ({
  buildSheetPrompt: vi.fn(),
  rankSheets: vi.fn(),
  useSheets: vi.fn(),
  listSheetDraws: vi.fn(),
  createSheetDraw: vi.fn(),
  updateSheetDraw: vi.fn(),
  deleteSheetDraw: vi.fn(),
  listSheetDrawSummary: vi.fn(),
  getCharacter: vi.fn(),
  getReferencePictures: vi.fn(),
  patchCharacter: vi.fn(),
  listAdapters: vi.fn(),
  getWorkflowCard: vi.fn(),
  listWorkflowCards: vi.fn(),
  preflightWorkflowRun: vi.fn(),
  runWorkflowCard: vi.fn(),
  sheetCopyWorkflow: vi.fn(),
}));
vi.mock("../../api/characterSheets", () => ({
  buildSheetPrompt: gen.buildSheetPrompt,
  rankSheets: gen.rankSheets,
  useSheets: gen.useSheets,
  listSheetDraws: gen.listSheetDraws,
  createSheetDraw: gen.createSheetDraw,
  updateSheetDraw: gen.updateSheetDraw,
  deleteSheetDraw: gen.deleteSheetDraw,
  listSheetDrawSummary: gen.listSheetDrawSummary,
}));
vi.mock("../../api/characters", () => ({
  getCharacter: gen.getCharacter,
  getReferencePictures: gen.getReferencePictures,
  patchCharacter: gen.patchCharacter,
}));
vi.mock("../../api/modelShelf", () => ({ listAdapters: gen.listAdapters }));
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
}));
vi.mock("../../api/workflows", () => ({
  getWorkflowCard: gen.getWorkflowCard,
  listWorkflowCards: gen.listWorkflowCards,
  preflightWorkflowRun: gen.preflightWorkflowRun,
  runWorkflowCard: gen.runWorkflowCard,
  sheetCopyWorkflow: gen.sheetCopyWorkflow,
}));

import CharacterSheets from "./CharacterSheets.vue";
import PicturePicker from "../widgets/PicturePicker.vue";

const MOSS_CARD = {
  id: "manual:moss",
  name: "MOSS sheet",
  type: "txt2img",
  last_used: "2026-11-09T20:00:00Z",
  models: [{ base_model_family: "moss_image" }],
};
const QWEN_SHEET = {
  id: "manual:qwen-sheet",
  name: "Qwen edit (sheet)",
  type: "img2img",
  reference_inputs: 1,
  models: [{ base_model_family: "qwen" }],
};
const QWEN_EDIT = {
  id: "auto:qwen-edit",
  name: "Qwen edit",
  type: "img2img",
  reference_inputs: 0,
  models: [{ base_model_family: "qwen" }],
};

// ── The draws server ──
let db;

const copy = (value) => JSON.parse(JSON.stringify(value));
const ended = (run) => ["completed", "failed"].includes(run.status);

function settle(draw) {
  for (const run of draw.runs) {
    const outcome = db.outcomes[run.key];
    if (ended(run) || !outcome) continue;
    run.status = outcome.status;
    run.message = outcome.message || "";
    run.picture_ids = outcome.picture_ids || [];
  }
}

function storedDraw(fields) {
  const draw = {
    id: db.nextId++,
    character_id: 7,
    created_at: new Date().toISOString(),
    settings: {},
    drawn_from: null,
    runs: [],
    kept: [],
    used: [],
    used_sheets: [],
    seen: false,
    ...fields,
  };
  db.draws.push(draw);
  return draw;
}

function serveDraws() {
  db = { draws: [], nextId: 1, outcomes: {} };
  gen.listSheetDraws.mockImplementation(async () => {
    db.draws.forEach(settle);
    return { draws: copy([...db.draws].reverse()) };
  });
  gen.createSheetDraw.mockImplementation(async (_cid, body) => {
    const draw = storedDraw({
      settings: body.settings,
      drawn_from: body.drawn_from,
      runs: body.run_keys.map((key) => ({
        key,
        status: "queued",
        message: "",
        picture_ids: [],
      })),
    });
    settle(draw);
    return copy(draw);
  });
  gen.updateSheetDraw.mockImplementation(async (_cid, id, patch) => {
    const draw = db.draws.find((d) => d.id === id);
    for (const key of patch.add_run_keys || []) {
      draw.runs.push({ key, status: "queued", message: "", picture_ids: [] });
    }
    if (patch.kept) draw.kept = patch.kept;
    if (patch.seen != null) draw.seen = patch.seen;
    settle(draw);
    return copy(draw);
  });
  gen.deleteSheetDraw.mockImplementation(async (_cid, id) => {
    db.draws = db.draws.filter((d) => d.id !== id);
    return { deleted: true };
  });
  gen.listSheetDrawSummary.mockResolvedValue({ characters: [] });
}

/** One MOSS workflow, LoRAs newest first, nothing drawn yet. */
function sheetsWorld({ description = "", loras = [], imageCount = 0 } = {}) {
  serveDraws();
  gen.getCharacter.mockResolvedValue({
    id: 7,
    name: "Anna",
    description,
    image_count: imageCount,
  });
  gen.getReferencePictures.mockResolvedValue({ reference_picture_ids: [] });
  gen.patchCharacter.mockResolvedValue({ status: "success" });
  gen.listAdapters.mockResolvedValue(loras);
  gen.listWorkflowCards.mockResolvedValue({
    cards: [MOSS_CARD, { id: "auto:sdxl", name: "SDXL", type: "txt2img", models: [] }],
  });
  gen.getWorkflowCard.mockResolvedValue({
    card: {
      name: "MOSS sheet",
      defaults: [
        { slot_label: "ks", input_name: "steps", value: 25 },
        { slot_label: "lat", input_name: "width", value: 1024 },
        { slot_label: "lat", input_name: "height", value: 1024 },
      ],
    },
  });
  gen.preflightWorkflowRun.mockResolvedValue({ ok: true, groups: [{ reasons: [] }] });
  gen.buildSheetPrompt.mockResolvedValue({
    prompt: "{layout}",
    negative: "nsfw",
    width: 2560,
    height: 1536,
  });
  gen.runWorkflowCard.mockResolvedValue({
    status: "success",
    prompts: [
      { run_id: "r1", status: "queued" },
      { run_id: "r2", status: "queued" },
    ],
  });
}

/** sheetsWorld plus reference faces and what the library's cards are. */
function referenceWorld({ cards = [MOSS_CARD, QWEN_SHEET], faces = [31, 32] } = {}) {
  sheetsWorld({ description: "Adult woman, copper bob" });
  gen.getReferencePictures.mockResolvedValue({ reference_picture_ids: faces });
  gen.listWorkflowCards.mockResolvedValue({ cards });
  // Its picture input is open until the draw fills it.
  gen.preflightWorkflowRun.mockResolvedValue({
    ok: false,
    groups: [
      {
        reasons: [{ code: "picture_input_unfilled" }],
        picture_inputs: [
          { slot_label: "load", input_name: "image", mode: "picker" },
          { slot_label: "pinned", input_name: "image", mode: "fixed" },
        ],
        prompt: { positive_settable: true },
      },
    ],
  });
  gen.runWorkflowCard.mockResolvedValue({
    status: "success",
    prompts: [{ run_id: "r1", status: "queued" }],
  });
  db.outcomes.r1 = { status: "completed", picture_ids: [501, 502] };
}

function sheet(id, agreement, extra = {}) {
  return {
    picture_id: id,
    faces_read: true,
    faces: 6,
    agreement,
    agreement_words: agreement >= 0.55 ? "faces agree strongly" : "faces agree somewhat",
    closeness: null,
    close: null,
    broken: false,
    ...extra,
  };
}

function mountScreen(stubs = {}) {
  return mount(CharacterSheets, {
    props: { characterId: 7 },
    global: { stubs: { teleport: true, TbGlobalActions: true, ...stubs } },
    attachTo: document.body,
  });
}

function button(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().startsWith(text));
}

const drawButton = (wrapper) => wrapper.find('[data-testid="cs-draw-button"]');
const useButton = (wrapper) => wrapper.find('[data-testid="cs-use"]');
const tiles = (wrapper) => wrapper.findAll('[data-testid="cs-sheet"]');
const drawn = (wrapper) =>
  wrapper.findAll('[data-testid="cs-sheet"] img').map((i) => i.attributes("src"));

async function describeAndDraw(wrapper, text = "x") {
  await wrapper.find('[data-testid="cs-description"] textarea').setValue(text);
  await drawButton(wrapper).trigger("click");
  await flushPromises();
}

beforeEach(() => {
  vi.clearAllMocks();
  setActivePinia(createPinia());
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("CharacterSheets", () => {
  it("opens empty for a character with no draws; drawing waits for a description", async () => {
    sheetsWorld();
    const wrapper = mountScreen();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-empty"]').text()).toContain(
      "No sheets for Anna yet",
    );
    expect(drawButton(wrapper).text()).toContain("Draw 4 sheets");
    expect(drawButton(wrapper).attributes("disabled")).toBeDefined();
    expect(useButton(wrapper).attributes("disabled")).toBeDefined();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
    expect(drawButton(wrapper).attributes("disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("comes back to a stored draw: its sheets, its kept marks, its form, and the runs still going", async () => {
    sheetsWorld();
    // Yesterday's draw: three sheets, none kept.
    const older = storedDraw({
      created_at: "2026-11-01T10:00:00Z",
      settings: { signature: "old", description: "older" },
      runs: [
        { key: "o1", status: "completed", message: "", picture_ids: [401, 402, 403] },
      ],
      seen: true,
    });
    // Today's, left while it was drawing: one sheet in and kept, one queued.
    const newest = storedDraw({
      settings: {
        signature: "",
        description: "Adult woman, copper bob",
        body: "slim build",
        framing: "full_body",
        count: 2,
        size: "large",
        subject: "woman",
        mode: "text",
        workflow_id: "manual:moss",
        lora_sha: "",
        strength: 0.8,
        reference_id: null,
      },
      runs: [
        { key: "n1", status: "completed", message: "", picture_ids: [501] },
        { key: "n2", status: "queued", message: "", picture_ids: [] },
      ],
      kept: [501],
    });
    gen.rankSheets.mockImplementation(async (_cid, ids) => ({
      sheets: ids.map((id) => sheet(id, 0.6)),
      references: 0,
    }));
    const wrapper = mountScreen();
    await flushPromises();

    // The form is what the newest draw was drawn with.
    expect(
      wrapper.find('[data-testid="cs-description"] textarea').element.value,
    ).toBe("Adult woman, copper bob");
    // The newest on top, its sheet where it landed and kept, its run waiting.
    const sections = wrapper.findAll('[data-testid="cs-draw"]');
    expect(sections.map((s) => s.attributes("data-draw"))).toEqual([
      String(newest.id),
      String(older.id),
    ]);
    expect(sections[0].text()).toContain("1 queued");
    expect(sections[0].findAll('[data-testid="cs-slot"]').map((s) => s.text())).toEqual([
      "queued",
    ]);
    expect(sections[0].find('[data-testid="cs-sheet"]').attributes("aria-pressed")).toBe(
      "true",
    );
    expect(useButton(wrapper).text()).toContain("Use sheet 1");
    // The earlier draw stays listed, dimmed, and can still be picked from.
    expect(sections[1].classes()).toContain("cs-draw--past");
    expect(sections[1].text()).toContain("3 sheets · none kept");
    await sections[1].findAll('[data-testid="cs-sheet"]')[1].trigger("click");
    await flushPromises();
    expect(gen.updateSheetDraw).toHaveBeenCalledWith(7, older.id, { kept: [402] });
    expect(useButton(wrapper).text()).toContain("Use 2 sheets");

    // The run that kept going lands in its place.
    db.outcomes.n2 = { status: "completed", picture_ids: [502] };
    await vi.advanceTimersByTimeAsync(3000);
    expect(
      wrapper
        .findAll('[data-testid="cs-draw"]')[0]
        .findAll('[data-testid="cs-sheet"] img')
        .map((i) => i.attributes("src")),
    ).toEqual(["/thumb/501.webp", "/thumb/502.webp"]);
    // Ended: seen, and nothing kept for the user over the owner's own mark.
    expect(gen.updateSheetDraw).toHaveBeenCalledWith(7, newest.id, { seen: true });
    wrapper.unmount();
  });

  it("saves quick ticks one after another and loses none when the first fails", async () => {
    sheetsWorld();
    const draw = storedDraw({
      settings: { signature: "old", description: "older" },
      runs: [{ key: "o1", status: "completed", message: "", picture_ids: [401, 402] }],
      seen: true,
    });
    gen.rankSheets.mockImplementation(async (_cid, ids) => ({
      sheets: ids.map((id) => sheet(id, 0.6)),
      references: 0,
    }));
    const wrapper = mountScreen();
    await flushPromises();
    const serve = gen.updateSheetDraw.getMockImplementation();
    let failFirst;
    gen.updateSheetDraw.mockImplementationOnce(
      () => new Promise((_resolve, reject) => (failFirst = reject)),
    );

    await tiles(wrapper)[0].trigger("click");
    await tiles(wrapper)[1].trigger("click");
    await flushPromises();
    // The second waits for the first, so the server ends on the last tick.
    expect(gen.updateSheetDraw).toHaveBeenCalledTimes(1);
    gen.updateSheetDraw.mockImplementation(serve);
    failFirst(new Error("offline"));
    await flushPromises();

    expect(gen.updateSheetDraw).toHaveBeenLastCalledWith(7, draw.id, { kept: [401, 402] });
    expect(db.draws[0].kept).toEqual([401, 402]);
    expect(tiles(wrapper).map((t) => t.attributes("aria-pressed"))).toEqual([
      "true",
      "true",
    ]);
    wrapper.unmount();
  });

  it("refuses a workflow with no picture size, or one that can't run, before drawing", async () => {
    sheetsWorld({ description: "Adult woman, copper bob" });
    gen.getWorkflowCard.mockResolvedValue({
      card: { name: "MOSS sheet", defaults: [{ slot_label: "ks", input_name: "steps", value: 25 }] },
    });
    let wrapper = mountScreen();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').text()).toContain(
      "has no picture size",
    );
    expect(drawButton(wrapper).attributes("disabled")).toBeDefined();
    wrapper.unmount();

    sheetsWorld({ description: "Adult woman, copper bob" });
    gen.preflightWorkflowRun.mockResolvedValue({
      ok: false,
      groups: [{ reasons: [{ code: "missing_models", models: [] }] }],
    });
    wrapper = mountScreen();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').text()).toContain(
      "can't run as it is",
    );
    expect(drawButton(wrapper).attributes("disabled")).toBeDefined();
    wrapper.unmount();

    // The positive control: a sized workflow that runs can draw.
    sheetsWorld({ description: "Adult woman, copper bob" });
    wrapper = mountScreen();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').exists()).toBe(false);
    expect(drawButton(wrapper).attributes("disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("draws with the workflow chosen when Draw was pressed, not one picked meanwhile", async () => {
    sheetsWorld({ description: "Adult woman, copper bob" });
    let built;
    gen.buildSheetPrompt.mockReturnValueOnce(new Promise((r) => (built = r)));
    const wrapper = mountScreen();
    await flushPromises();
    await drawButton(wrapper).trigger("click");
    gen.getWorkflowCard.mockResolvedValue({ card: { name: "SDXL", defaults: [] } });
    wrapper
      .findAllComponents({ name: "AppSelect" })
      .find((c) => c.props("label") === "Sheet workflow")
      .vm.$emit("update:modelValue", "auto:sdxl");
    await flushPromises();
    built({ prompt: "{layout}", negative: "nsfw", width: 2560, height: 1536 });
    await flushPromises();
    expect(gen.runWorkflowCard).toHaveBeenCalledWith(
      expect.objectContaining({
        workflow_id: "manual:moss",
        values: [
          { slot_label: "lat", input_name: "width", value: 2560 },
          { slot_label: "lat", input_name: "height", value: 1536 },
        ],
      }),
    );
    expect(gen.createSheetDraw).toHaveBeenCalledWith(
      7,
      expect.objectContaining({
        run_keys: ["r1", "r2"],
        settings: expect.objectContaining({ workflow_id: "manual:moss" }),
      }),
    );
    wrapper.unmount();
  });

  it("draws, stores the draw, keeps the best for the user and cuts it", async () => {
    sheetsWorld({
      loras: [
        // Newest, but for another model: listed, never preselected.
        { sha256: "sdxl", display_name: "Anna SDXL", base_model_family: "sdxl", added_at: "2026-11-09" },
        { sha256: "moss1", display_name: "Anna v1", base_model_family: "moss_image", trigger_words: ["anna"], added_at: "2026-11-08" },
      ],
    });
    const wrapper = mountScreen();
    await flushPromises();
    await describeAndDraw(wrapper, "Adult woman, copper bob");

    // No description on the character: the typed one becomes hers.
    expect(gen.patchCharacter).toHaveBeenCalledWith(7, {
      description: "Adult woman, copper bob",
    });
    expect(gen.buildSheetPrompt).toHaveBeenCalledWith(
      expect.objectContaining({ trigger: "anna", framing: "full_body", size: "large" }),
    );
    expect(gen.runWorkflowCard).toHaveBeenCalledWith(
      expect.objectContaining({
        workflow_id: "manual:moss",
        prompt: "{layout}",
        count: 4,
        character_ids: [],
        hold: true,
        label: "Character sheet · Anna",
        add_loras: [{ sha256: "moss1", strength_model: 0.8, strength_clip: 0.8 }],
      }),
    );
    expect(gen.createSheetDraw).toHaveBeenCalledWith(7, {
      settings: expect.objectContaining({
        description: "Adult woman, copper bob",
        lora_sha: "moss1",
      }),
      drawn_from: null,
      run_keys: ["r1", "r2"],
    });

    db.outcomes.r1 = { status: "completed", picture_ids: [501] };
    db.outcomes.r2 = { status: "completed", picture_ids: [502] };
    gen.rankSheets.mockResolvedValue({
      sheets: [sheet(501, 0.5), sheet(502, 0.66)],
      references: 0,
    });
    await vi.advanceTimersByTimeAsync(3000);
    // Each in its run's place, never reordered; the best is kept there.
    expect(tiles(wrapper).map((b) => b.attributes("aria-label"))).toEqual([
      "Sheet 1: faces agree somewhat",
      "Sheet 2: faces agree strongly · kept",
    ]);
    expect(tiles(wrapper)[1].attributes("aria-pressed")).toBe("true");
    expect(gen.updateSheetDraw).toHaveBeenCalledWith(7, 1, { seen: true, kept: [502] });
    expect(useButton(wrapper).text()).toContain("Use sheet 2");

    gen.useSheets.mockResolvedValue({ picture_ids: [601, 602] });
    gen.rankSheets.mockResolvedValue({
      sheets: [sheet(601, null), sheet(602, null)],
      references: 0,
    });
    await useButton(wrapper).trigger("click");
    await flushPromises();
    expect(gen.useSheets).toHaveBeenCalledWith(7, [502], 1);
    expect(wrapper.find('[data-testid="cs-filed"]').text()).toContain(
      "2 pictures filed on Anna",
    );
    await wrapper.find('[data-testid="cs-create-set"]').trigger("click");
    expect(wrapper.emitted("create-training-set")).toEqual([[{ id: 7, name: "Anna" }]]);
    wrapper.unmount();
  });

  it("stops waiting for faces that never come and lets the sheet be used", async () => {
    sheetsWorld();
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: [{ run_id: "r1", status: "queued" }],
    });
    db.outcomes.r1 = { status: "completed", picture_ids: [501] };
    gen.rankSheets.mockResolvedValue({
      sheets: [{ ...sheet(501, null), faces_read: false, faces: 0 }],
      references: 0,
    });
    const wrapper = mountScreen();
    await flushPromises();
    await describeAndDraw(wrapper);
    await vi.advanceTimersByTimeAsync(10_000);
    const tile = () => wrapper.find('[data-testid="cs-sheet"]');
    expect(tile().attributes("aria-label")).toBe("Sheet 1: reading faces…");
    expect(tile().attributes("disabled")).toBeDefined();

    await vi.advanceTimersByTimeAsync(125_000);
    expect(tile().attributes("aria-label")).toBe("Sheet 1: faces not read");
    expect(tile().attributes("disabled")).toBeUndefined();
    // Polling has stopped.
    const calls = gen.rankSheets.mock.calls.length;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(gen.rankSheets.mock.calls.length).toBe(calls);
    await tile().trigger("click");
    await flushPromises();
    expect(useButton(wrapper).attributes("disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("adds Draw more to the newest draw, but a new description starts a new draw on top", async () => {
    sheetsWorld();
    db.outcomes = {
      r1: { status: "completed", picture_ids: [501] },
      r2: { status: "completed", picture_ids: [502] },
      r3: { status: "completed", picture_ids: [503] },
    };
    gen.rankSheets.mockImplementation(async (_id, ids) => ({
      sheets: ids.map((id) => sheet(id, 0.6)),
      references: 0,
    }));
    const startsRun = (key) =>
      gen.runWorkflowCard.mockResolvedValueOnce({
        status: "success",
        prompts: [{ run_id: key, status: "queued" }],
      });
    const newestSheets = () =>
      wrapper
        .findAll('[data-testid="cs-draw"]')[0]
        .findAll('[data-testid="cs-sheet"] img')
        .map((i) => i.attributes("src"));
    const wrapper = mountScreen();
    await flushPromises();
    startsRun("r1");
    await describeAndDraw(wrapper);
    await vi.advanceTimersByTimeAsync(3000);
    expect(newestSheets()).toEqual(["/thumb/501.webp"]);

    // The same settings again: more of the same, in the same draw.
    expect(drawButton(wrapper).text()).toContain("Draw 4 more");
    startsRun("r2");
    await drawButton(wrapper).trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    expect(gen.updateSheetDraw).toHaveBeenCalledWith(7, 1, { add_run_keys: ["r2"] });
    expect(newestSheets()).toEqual(["/thumb/501.webp", "/thumb/502.webp"]);

    // Edited and drawn again: a new draw on top, the earlier one kept below.
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("y");
    expect(drawButton(wrapper).text()).toContain("Draw 4 sheets");
    startsRun("r3");
    await drawButton(wrapper).trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    expect(gen.createSheetDraw).toHaveBeenCalledTimes(2);
    expect(newestSheets()).toEqual(["/thumb/503.webp"]);
    expect(drawn(wrapper)).toHaveLength(3);
    expect(gen.rankSheets).toHaveBeenLastCalledWith(7, [503]);
    wrapper.unmount();
  });

  it("keeps each sheet in its run's place and shows the drawing one's progress", async () => {
    sheetsWorld();
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: ["r1", "r2", "r3", "r4"].map((run_id) => ({ run_id, status: "queued" })),
    });
    gen.rankSheets.mockImplementation(async (_id, ids) => ({
      sheets: ids.map((id) => sheet(id, 0.6)),
      references: 0,
    }));
    const tasks = useTasksStore();
    tasks.comfyuiRuns = [
      { run_id: "r1", status: "running", percent: 40 },
      { run_id: "r2", status: "queued", percent: 0 },
    ];
    const places = () =>
      wrapper
        .findAll(".cs-sheets > li")
        .map((li) => (li.find("img").exists() ? li.find("img").attributes("src") : li.text()));
    const wrapper = mountScreen();
    await flushPromises();
    await describeAndDraw(wrapper);

    // The second run finishes first: its sheet takes the second place.
    db.outcomes.r2 = { status: "completed", picture_ids: [502] };
    await vi.advanceTimersByTimeAsync(3000);
    expect(places()).toEqual(["drawing", "/thumb/502.webp", "queued", "queued"]);
    expect(
      wrapper.find('[aria-label="Drawing"][role="progressbar"]').attributes("aria-valuenow"),
    ).toBe("40");
    // The draw's own line: one of four runs done and the second at 40%.
    expect(
      wrapper.find('[aria-label="This draw"]').attributes("aria-valuenow"),
    ).toBe("35");

    db.outcomes.r1 = { status: "completed", picture_ids: [501] };
    db.outcomes.r4 = { status: "completed", picture_ids: [504] };
    await vi.advanceTimersByTimeAsync(3000);
    expect(places()).toEqual([
      "/thumb/501.webp",
      "/thumb/502.webp",
      "queued",
      "/thumb/504.webp",
    ]);
    expect(wrapper.find('[aria-label="Zoom in on sheet 4"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("zooms a sheet in a pop-out; Escape leaves only the pop-out; Keep there keeps it", async () => {
    sheetsWorld();
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: [{ run_id: "r1", status: "queued" }],
    });
    db.outcomes.r1 = { status: "completed", picture_ids: [501, 502] };
    gen.rankSheets.mockResolvedValue({
      sheets: [sheet(501, 0.7, { format: "png" }), sheet(502, 0.5, { format: "png" })],
      references: 0,
    });
    const popout = () => document.querySelector('[data-testid="zoom-popout"]');
    const wrapper = mountScreen();
    await flushPromises();
    await describeAndDraw(wrapper);
    await vi.advanceTimersByTimeAsync(3000);

    // Clicking the sheet ticks it; only its magnifier zooms.
    expect(popout()).toBeNull();
    await wrapper.findAll('[data-testid="cs-zoom-open"]')[1].trigger("click");
    await flushPromises();
    expect(popout().querySelector("img").getAttribute("src")).toMatch(/\/pictures\/502\.png$/);
    popout().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    expect(popout()).toBeNull();
    expect(tiles(wrapper)).toHaveLength(2);

    await wrapper.findAll('[data-testid="cs-zoom-open"]')[1].trigger("click");
    await flushPromises();
    const choice = () =>
      Object.fromEntries(
        [...popout().querySelectorAll('[role="radio"]')].map((b) => [
          b.querySelector(".seg__label").textContent,
          b.getAttribute("aria-checked"),
        ]),
      );
    expect(choice()).toEqual({ Keep: "false", Drop: "true" });
    [...popout().querySelectorAll('[role="radio"]')]
      .find((b) => b.textContent.includes("Keep"))
      .click();
    await flushPromises();
    expect(choice()).toEqual({ Keep: "true", Drop: "false" });
    expect(useButton(wrapper).text()).toContain("Use 2 sheets");

    // Left steps to the sheet before, in its place.
    popout().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    await flushPromises();
    expect(popout().querySelector("img").getAttribute("src")).toMatch(/\/pictures\/501\.png$/);
    popout().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();

    // Wheel up over a sheet opens it; wheel down leaves the page to scroll.
    await tiles(wrapper)[0].trigger("wheel", { deltaY: 100 });
    expect(popout()).toBeNull();
    await tiles(wrapper)[0].trigger("wheel", { deltaY: -100 });
    await flushPromises();
    expect(popout().querySelector("img").getAttribute("src")).toMatch(/\/pictures\/501\.png$/);
    wrapper.unmount();
  });

  it("warns, without refusing, when the sheets kept look like different people", async () => {
    const keepBoth = async (ranked) => {
      sheetsWorld();
      gen.runWorkflowCard.mockResolvedValue({
        status: "success",
        prompts: [{ run_id: "r1", status: "queued" }],
      });
      db.outcomes.r1 = { status: "completed", picture_ids: [501, 502] };
      gen.rankSheets.mockResolvedValue({ sheets: ranked, references: 0 });
      const wrapper = mountScreen();
      await flushPromises();
      await describeAndDraw(wrapper);
      await vi.advanceTimersByTimeAsync(3000);
      return wrapper;
    };
    const warning = (w) => w.find('[data-testid="cs-mixed"]');

    let wrapper = await keepBoth([
      sheet(501, 0.5, { different_from: [502] }),
      sheet(502, 0.7, { different_from: [501] }),
    ]);
    // One kept: nothing to mix.
    expect(warning(wrapper).exists()).toBe(false);
    await tiles(wrapper)[0].trigger("click");
    await flushPromises();
    expect(warning(wrapper).text()).toContain("Sheets 1 and 2 look like different people");
    expect(useButton(wrapper).text()).toContain("Use 2 sheets");
    expect(useButton(wrapper).attributes("disabled")).toBeUndefined();
    wrapper.unmount();

    // The positive control: two sheets of one person, kept together, pass.
    wrapper = await keepBoth([sheet(501, 0.5), sheet(502, 0.7)]);
    await tiles(wrapper)[0].trigger("click");
    await flushPromises();
    expect(useButton(wrapper).text()).toContain("Use 2 sheets");
    expect(warning(wrapper).exists()).toBe(false);
    wrapper.unmount();
  });

  it("deletes a draw's record and leaves its sheets alone", async () => {
    sheetsWorld();
    storedDraw({
      runs: [{ key: "o1", status: "completed", message: "", picture_ids: [401] }],
      seen: true,
    });
    gen.rankSheets.mockResolvedValue({ sheets: [sheet(401, 0.6)], references: 0 });
    const wrapper = mountScreen();
    await flushPromises();
    await wrapper.find('[data-testid="cs-draw-delete"]').trigger("click");
    await flushPromises();
    expect(gen.deleteSheetDraw).toHaveBeenCalledWith(7, 1);
    expect(wrapper.findAll('[data-testid="cs-draw"]')).toHaveLength(0);
    expect(wrapper.find('[data-testid="cs-empty"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("keeps a character's own description and says the sheets are a new likeness", async () => {
    sheetsWorld({ description: "Woman in her thirties, dark hair", imageCount: 300 });
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: [{ run_id: "r1", status: "queued" }],
    });
    const wrapper = mountScreen();
    await flushPromises();
    expect(
      wrapper.find('[data-testid="cs-description"] textarea').element.value,
    ).toBe("Woman in her thirties, dark hair");
    expect(wrapper.find('[data-testid="cs-likeness"]').text()).toContain(
      "drawn from words, not from Anna's 300 pictures",
    );
    await drawButton(wrapper).trigger("click");
    await flushPromises();
    expect(gen.patchCharacter).not.toHaveBeenCalled();
    expect(gen.buildSheetPrompt).toHaveBeenCalledWith(
      expect.objectContaining({ framing: "mid_thigh", trigger: "" }),
    );
    wrapper.unmount();
  });

  // ── Drawn from a reference picture (#2679) ──

  const mode = (wrapper) => wrapper.find('[data-testid="cs-mode"] [aria-checked="true"]');

  it("offers the reference mode only where a reference workflow exists", async () => {
    referenceWorld({ cards: [MOSS_CARD] });
    let wrapper = mountScreen();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-mode"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="cs-reference"]').exists()).toBe(false);
    wrapper.unmount();

    // A card, but a character with no pictures: offered, and text is chosen.
    referenceWorld({ faces: [] });
    wrapper = mountScreen();
    await flushPromises();
    expect(mode(wrapper).text()).toBe("Text");
    expect(wrapper.find('[data-testid="cs-reference-warning"]').exists()).toBe(false);
    wrapper.unmount();

    // A card and a face: drawn from the first reference face, with the warning.
    referenceWorld();
    wrapper = mountScreen();
    await flushPromises();
    expect(mode(wrapper).text()).toBe("Reference and text");
    expect(wrapper.find('[data-testid="cs-reference"]').attributes("src")).toBe(
      "/thumb/31.webp",
    );
    expect(wrapper.find('[data-testid="cs-reference-warning"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="cs-likeness"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("feeds the reference through the run's inputs and ranks against it", async () => {
    referenceWorld();
    gen.rankSheets.mockResolvedValue({
      sheets: [
        // Both close; the first agrees with itself far better.
        sheet(501, 0.95, { closeness: 0.5, close: true }),
        sheet(502, 0.5, { closeness: 0.74, close: true }),
      ],
      references: 1,
    });
    const wrapper = mountScreen();
    await flushPromises();
    await drawButton(wrapper).trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    expect(gen.buildSheetPrompt).toHaveBeenCalledWith(
      expect.objectContaining({ reference: true, layout: "prose" }),
    );
    const body = gen.runWorkflowCard.mock.calls[0][0];
    expect(body.workflow_id).toBe("manual:qwen-sheet");
    // The open input takes the picture; the pinned one keeps its pin.
    expect(body.inputs).toEqual([{ slot_label: "load", input_name: "image", picture_id: 31 }]);
    expect(gen.createSheetDraw).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ drawn_from: 31 }),
    );
    expect(gen.rankSheets).toHaveBeenLastCalledWith(7, [501, 502], [31]);
    // Closeness decides: the closer sheet is kept, though its faces agree less.
    expect(tiles(wrapper).map((b) => b.attributes("aria-pressed"))).toEqual(["false", "true"]);
    expect(tiles(wrapper)[1].attributes("aria-label")).toBe(
      "Sheet 2: 0.74 close to the reference · faces agree somewhat · kept",
    );
    expect(wrapper.findAll('[data-testid="cs-closeness"]')[0].text()).toContain(
      "0.50close to the reference",
    );
    expect(wrapper.find('[data-testid="cs-none-close"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("keeps no sheet for the user when none comes close to the reference", async () => {
    referenceWorld();
    gen.rankSheets.mockResolvedValue({
      sheets: [
        sheet(501, 0.7, { closeness: 0.31, close: false }),
        sheet(502, 0.6, { closeness: 0.4, close: false }),
      ],
      references: 1,
    });
    const wrapper = mountScreen();
    await flushPromises();
    await drawButton(wrapper).trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    expect(tiles(wrapper).map((b) => b.attributes("aria-pressed"))).toEqual(["false", "false"]);
    expect(wrapper.find('[data-testid="cs-none-close"]').text()).toContain(
      "None of the 2 sheets came close to the reference",
    );
    expect(gen.updateSheetDraw).toHaveBeenCalledWith(7, 1, { seen: true });
    wrapper.unmount();
  });

  it("draws from another reference picture as a new draw on top", async () => {
    referenceWorld();
    gen.rankSheets.mockImplementation(async (_id, ids) => ({
      sheets: ids.map((id) => sheet(id, 0.6, { closeness: 0.7, close: true })),
      references: 1,
    }));
    const wrapper = mountScreen();
    await flushPromises();
    await drawButton(wrapper).trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    expect(tiles(wrapper)).toHaveLength(2);

    await wrapper.findAll('[data-testid="cs-reference-choice"]')[1].trigger("click");
    gen.runWorkflowCard.mockResolvedValueOnce({
      status: "success",
      prompts: [{ run_id: "r2", status: "queued" }],
    });
    db.outcomes.r2 = { status: "completed", picture_ids: [503] };
    await drawButton(wrapper).trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    expect(gen.runWorkflowCard.mock.calls[1][0].inputs[0].picture_id).toBe(32);
    expect(
      wrapper
        .findAll('[data-testid="cs-draw"]')[0]
        .findAll('[data-testid="cs-sheet"] img')
        .map((i) => i.attributes("src")),
    ).toEqual(["/thumb/503.webp"]);
    expect(gen.rankSheets).toHaveBeenLastCalledWith(7, [503], [32]);
    wrapper.unmount();
  });

  it("hands focus back to the pick button when the picture picker closes", async () => {
    referenceWorld();
    const wrapper = mountScreen({ PicturePicker: true });
    await flushPromises();
    const pick = () => wrapper.find('[data-testid="cs-reference-pick"]');
    for (const [event, payload] of [["close"], ["pick", { id: 77 }]]) {
      await pick().trigger("click");
      await flushPromises();
      pick().element.blur();
      wrapper.findComponent(PicturePicker).vm.$emit(event, payload);
      await flushPromises();
      expect(wrapper.findComponent(PicturePicker).exists()).toBe(false);
      expect(document.activeElement).toBe(pick().element);
    }
    expect(wrapper.find('[data-testid="cs-reference"]').attributes("src")).toBe(
      "/thumb/77.webp",
    );
    wrapper.unmount();
  });

  it("Change… opens the faces dialog and shows the new reference faces after", async () => {
    referenceWorld();
    const wrapper = mountScreen({ FaceClustersDialog: true });
    await flushPromises();
    const dialog = () => wrapper.findComponent({ name: "FaceClustersDialog" });
    expect(dialog().props("open")).toBe(false);
    await wrapper.find('[data-testid="cs-change-faces"]').trigger("click");
    expect(dialog().props("open")).toBe(true);
    gen.getReferencePictures.mockResolvedValue({ reference_picture_ids: [41] });
    dialog().vm.$emit("close");
    await flushPromises();
    expect(dialog().props("open")).toBe(false);
    expect(wrapper.find('[data-testid="cs-reference"]').attributes("src")).toBe(
      "/thumb/41.webp",
    );
    wrapper.unmount();
  });

  it("refuses a reference workflow with nowhere to put the picture or the prompt", async () => {
    referenceWorld();
    gen.preflightWorkflowRun.mockResolvedValue({
      ok: true,
      groups: [{ reasons: [], picture_inputs: [], prompt: { positive_settable: true } }],
    });
    let wrapper = mountScreen();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').text()).toContain(
      "no picture input",
    );
    wrapper.unmount();

    referenceWorld();
    gen.preflightWorkflowRun.mockResolvedValue({
      ok: true,
      groups: [
        {
          reasons: [],
          picture_inputs: [{ slot_label: "load", input_name: "image", mode: "picker" }],
          prompt: { positive_settable: false },
        },
      ],
    });
    wrapper = mountScreen();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').text()).toContain("no prompt");
    expect(drawButton(wrapper).attributes("disabled")).toBeDefined();
    wrapper.unmount();
  });

  it("makes a sheet workflow from the library's Qwen edit workflow", async () => {
    referenceWorld({ cards: [MOSS_CARD, QWEN_EDIT] });
    gen.sheetCopyWorkflow.mockResolvedValue({ workflow_id: QWEN_SHEET.id });
    const wrapper = mountScreen();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-mode"]').exists()).toBe(false);
    gen.listWorkflowCards.mockResolvedValue({ cards: [MOSS_CARD, QWEN_EDIT, QWEN_SHEET] });
    await button(wrapper, "Make a sheet workflow from Qwen edit").trigger("click");
    await flushPromises();
    expect(gen.sheetCopyWorkflow).toHaveBeenCalledWith("auto:qwen-edit");
    // The mode is now offered; the one chosen does not change under the user.
    expect(mode(wrapper).text()).toBe("Text");
    expect(wrapper.find('[data-testid="cs-convert"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("goes to the reference mode when the sheet workflow is the only one", async () => {
    referenceWorld({ cards: [QWEN_EDIT] });
    gen.sheetCopyWorkflow.mockResolvedValue({ workflow_id: QWEN_SHEET.id });
    const wrapper = mountScreen();
    await flushPromises();
    gen.listWorkflowCards.mockResolvedValue({ cards: [QWEN_EDIT, QWEN_SHEET] });
    await button(wrapper, "Make a sheet workflow from Qwen edit").trigger("click");
    await flushPromises();
    expect(mode(wrapper).text()).toBe("Reference and text");
    expect(gen.getWorkflowCard).toHaveBeenCalledWith(QWEN_SHEET.id);
    wrapper.unmount();
  });
});
