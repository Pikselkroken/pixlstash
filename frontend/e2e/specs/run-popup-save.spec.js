import { mkdirSync } from "node:fs";
import { join } from "node:path";

import { test, expect } from "../fixtures/test.js";

// The Run popup on a workflow, and the Tasks tab's offer after a run (#1850):
// several people in a run, and saving what a run changed.
//
// The unit tests hold the logic. What they cannot see is here, because jsdom
// lays nothing out and focuses nothing it is not told to: that the
// trigger-word line and the band stay on one row at the popup's real widths,
// which colour a button is, where the keyboard is after a row or a band goes,
// and that a closed Save cannot be pressed.
//
// The fixture library has no workflows and no ComfyUI, so every route the
// popup and the inspector behind it read is stubbed. A workflow route nobody
// stubbed is recorded and fails the test, so nothing here can pass on the
// real backend's 404.

const WF = `auto:${"a".repeat(64)}`;
const NEW_WF = `manual:${"b".repeat(32)}`;
const OTHER_WF = `auto:${"c".repeat(64)}`;
/** The workflow's last run, as the poll lists it under `finished` (#2050). */
const FINISHED = {
  id: "run-e2e-finished",
  workflow_id: WF,
  label: "Portrait, soft window light",
  ended_at: new Date().toISOString(),
  status: "completed",
  message: null,
  made: 2,
  // Two of the e2e library's pictures, for the tiles.
  picture_ids: [1, 2],
  offer: true,
};
const CKPT = "core:CheckpointLoader/ckpt_name";
const VAE = "core:VAELoader/vae_name";
const GRAIN_SHA = "1".repeat(64);
const DETAIL_SHA = "2".repeat(64);

/** Set to a directory to keep the screenshots; unset, none is written. */
const SHOTS = process.env.RUN_POPUP_SHOTS || "";

const def = (label, inputName, value, slotLabel = "KSampler") => ({
  label,
  slot_label: slotLabel,
  input_name: inputName,
  value,
  provenance: "best",
});

/** One person, the LoRA attached to them and the word it answers to. */
const person = (id, name, word) => ({
  id,
  name,
  lora: {
    id: 100 + id,
    sha256: String(id).repeat(64),
    filename: `${name.toLowerCase()}-sdxl.safetensors`,
    display_name: `${name.toLowerCase()}_sdxl`,
    base_model: "SDXL",
    base_model_family: "sdxl",
    trigger_words: [word],
    attachments: [{ entity_type: "character", entity_id: id }],
  },
});
const PEOPLE = [
  person(3, "Ada", "ada lovelace portrait woman"),
  person(4, "Bea", "beatrix-kiddo-character-v2"),
  person(5, "Cyd", "cydonia the red planet girl"),
  person(6, "Dot", "dotty"),
];
const STYLE_LORAS = [
  {
    id: 201,
    sha256: GRAIN_SHA,
    filename: "loras/film-grain-35mm.safetensors",
    display_name: "film-grain-35mm",
    base_model_family: "sdxl",
    attachments: [],
  },
  {
    id: 202,
    sha256: DETAIL_SHA,
    filename: "detail_slider_v2.safetensors",
    display_name: "detail_slider_v2",
    base_model_family: "sdxl",
    attachments: [],
  },
];

/** The workflow as `GET /workflows` and `GET /workflows/{id}` serve it. */
function workflowCard({ checkpoint = "realvisXL_v5.safetensors", steps = 28 } = {}) {
  return {
    id: WF,
    name: "Portrait, soft window light",
    type: "txt2img",
    type_label: "Text to Image",
    base_topology: "topology-a",
    topologies: ["topology-a"],
    models: [{ kind: "checkpoint", name: checkpoint, base_model_family: "sdxl" }],
    loras: [],
    picture_count: 12,
    rating: 4,
    rank: 1,
    covers: [],
    specials: ["upscale", "face_detailer"],
    unsaved_changes: 0,
    defaults: [
      def("Steps", "steps", steps),
      def("CFG", "cfg", 5),
      def("Width", "width", 832, "EmptyLatentImage"),
      def("Height", "height", 1216, "EmptyLatentImage"),
      def("Denoise", "denoise", 1),
    ],
    default_recipe: {
      sampled: 12,
      models: [
        { address: CKPT, kind: "checkpoint", filename: checkpoint, provenance: "best" },
        { address: VAE, kind: "vae", filename: "sdxl_vae.safetensors", provenance: "best" },
      ],
      loras: [
        { asset: "", filename: "detail_slider_v2.safetensors", sha256: DETAIL_SHA, strength: 0.6, provenance: "best" },
        { asset: "", filename: "loras/film-grain-35mm.safetensors", sha256: GRAIN_SHA, strength: 0.4, provenance: "best" },
      ],
      values: [],
      stages: { upscale: true, face_detailer: false },
      stage_details: {},
    },
  };
}

const loader = (node, filename, sha256, strength) => ({
  node_id: node,
  class_type: "LoraLoader",
  field: "lora_name",
  filename,
  name: filename.split("/").pop().replace(/\.safetensors$/, ""),
  strength,
  strength_clip: strength,
  sha256,
  on_shelf: true,
});
const CHAIN = [
  loader("7", "detail_slider_v2.safetensors", DETAIL_SHA, 0.6),
  loader("8", "loras/film-grain-35mm.safetensors", GRAIN_SHA, 0.4),
];

const OPEN = { open: true, reason: null };
const changesState = (overrides = {}) => ({
  workflow_id: WF,
  version: 3,
  waiting: null,
  count: 0,
  ran: null,
  save: { open: false, reason: "Nothing to save." },
  save_as_new: OPEN,
  ran_save: null,
  ...overrides,
});

const REPLACEMENTS = {
  checkpoint: [
    { id: 11, filename: "juggernautXL_v9.safetensors", display_name: "Juggernaut XL v9" },
    { id: 12, filename: "dreamshaperXL.safetensors", display_name: "DreamShaper XL" },
  ],
  vae: [
    { id: 21, filename: "sdxl_vae.safetensors", display_name: "SDXL VAE" },
    { id: 22, filename: "ae.safetensors", display_name: "Flux AE" },
  ],
};

/**
 * Stub the routes this screen reads and answer from *world*, which a test may
 * change as it goes. Returns what was sent: every save, run and dry run, and
 * every workflow request nothing here answers.
 */
async function stubWorkflow(page, world = {}) {
  const net = {
    world: {
      card: workflowCard(),
      chain: CHAIN,
      changes: changesState(),
      dryRun: { save: OPEN, save_as_new: OPEN },
      people: PEOPLE.slice(0, 2),
      /** Another workflow in the list, for the picker: `{ name }`. */
      second: null,
      /** Fields a save's answer carries besides the version: `still_waiting`, `not_kept`. */
      saveExtra: {},
      /** ComfyUI configured, so the sidebar offers "Create with LoRA…". */
      comfyui: false,
      /**
       * The poll's finished runs (#2050), or null to leave the poll alone.
       * A row offers its changes while `changes.ran` holds them, as the
       * server's `offer` follows the run set.
       */
      finished: null,
      ...world,
    },
    /** A held answer, by route: a gate to release (see `gate`). */
    hold: {},
    saves: [],
    savesAsNew: [],
    dryRuns: [],
    runs: [],
    waitingWrites: [],
    unstubbed: [],
  };
  const json = (route, body, status = 200) => route.fulfill({ status, json: body });

  await page.route(
    (url) => url.pathname.startsWith("/api/v1/"),
    async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = decodeURIComponent(url.pathname).slice("/api/v1".length);
      const method = request.method();
      const { world: now } = net;

      if (path === "/characters" && method === "GET") {
        return json(route, now.people.map(({ id, name }) => ({ id, name })));
      }
      if (/^\/characters\/\d+\/thumbnail$/.test(path)) {
        // A face for the tile: a flat disc, so the shots show the picked ring.
        return route.fulfill({
          contentType: "image/svg+xml",
          body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect width="8" height="8" fill="#8a6f5a"/><circle cx="4" cy="3" r="1.6" fill="#e7c9ae"/><rect x="1.6" y="5" width="4.8" height="3" rx="1.4" fill="#e7c9ae"/></svg>',
        });
      }
      if (path === "/adapters" && method === "GET") {
        // The popup asks for the LoRAs, and again for the files of no known
        // kind; a person's own are among the first. Asked for one person's,
        // as "Create with LoRA…" does, it is theirs alone.
        const fileKind = url.searchParams.get("file_kind");
        const characterId = Number(url.searchParams.get("character_id"));
        const adapters = characterId
          ? fileKind === "adapter"
            ? now.people.filter((entry) => entry.id === characterId).map((entry) => entry.lora)
            : []
          : fileKind
            ? []
            : [...STYLE_LORAS, ...now.people.map((entry) => entry.lora)];
        return json(route, { adapters });
      }
      if (path === "/recipes" && method === "GET") return json(route, []);
      if (now.finished && path === "/workers/progress" && method === "GET") {
        const real = await route.fetch();
        return route.fulfill({
          response: real,
          json: {
            ...(await real.json()),
            finished: now.finished.map((run) => ({ ...run, offer: Boolean(now.changes.ran) })),
          },
        });
      }
      if (now.comfyui && path === "/users/me/config" && method === "GET") {
        const real = await route.fetch();
        return route.fulfill({
          response: real,
          json: { ...(await real.json()), comfyui_url: "http://127.0.0.1:8188" },
        });
      }
      if (!path.startsWith("/workflows")) return route.fallback();

      if (path === "/workflows" && method === "GET") {
        const cards = [now.card];
        if (now.second) cards.push({ ...now.card, id: OTHER_WF, ...now.second });
        return json(route, { cards, one_offs: 0, hidden: 0 });
      }
      if (path === "/workflows/run/preflight" && method === "POST") {
        return json(route, {
          ok: true,
          runs: 1,
          groups: [
            {
              workflow_id: request.postDataJSON()?.workflow_id || WF,
              reasons: [],
              picture_inputs: [],
              prompt: {
                positive_text: "portrait by a tall window, soft overcast light",
                positive_settable: true,
                negative_settable: false,
              },
            },
          ],
        });
      }
      if (path === "/workflows/run" && method === "POST") {
        net.runs.push(request.postDataJSON());
        return json(route, {
          status: "success",
          prompts: [{ prompt_id: "p-1", workflow_id: WF }],
          groups: [],
        });
      }

      const own = [WF, NEW_WF, OTHER_WF].find((id) => path.startsWith(`/workflows/${id}`));
      const rest = own ? path.slice(`/workflows/${own}`.length) : null;
      if (rest === "" && method === "GET") {
        // The card of the workflow the picker moved to may be held on the wire.
        if (own === OTHER_WF && net.hold.card) await net.hold.card.promise;
        return json(route, {
          card: { ...now.card, id: own, ...(own === OTHER_WF ? now.second : {}) },
          notes: null,
          hidden: false,
          variants: [],
          pins: null,
          graph_base_models: [],
          model_fixes: [],
        });
      }
      if (rest === "/lora-chain" && method === "GET") {
        return json(route, {
          workflow_id: own,
          editable: true,
          refusal: null,
          source: null,
          sink: { summary: "", consumers: [] },
          loaders: now.chain,
          added_loader_class: "LoraLoader",
          lanes: [],
          branch_note: null,
        });
      }
      if (rest === "/changes" && method === "GET") {
        // The workflow a Save as new made has not been run: it has no `ran`
        // to offer. The receipt of that save is taken down when an offer
        // arrives after it, so a mock that answered the new workflow with the
        // old one's `ran` would make one arrive.
        if (own === NEW_WF) return json(route, changesState({ workflow_id: own }));
        return json(route, { ...now.changes, workflow_id: own });
      }
      if (rest === "/changes") {
        // The popup must never write what waits: recorded, and refused.
        net.waitingWrites.push(method);
        return json(route, { detail: "not from the Run popup" }, 409);
      }
      if (rest === "/form-inputs" && method === "GET") return json(route, { nodes: [] });
      if (rest === "/versions" && method === "GET") {
        return json(route, { workflow_id: own, version: now.changes.version, versions: [] });
      }
      if (rest === "/lora-summary" && method === "GET") {
        return json(route, {
          workflow_id: own,
          pictures: 12,
          shared: [],
          varying: [],
          no_lora: 0,
        });
      }
      if (rest === "/model-swap" && method === "GET") {
        return json(route, {
          slots: [],
          checkpoints: [],
          vaes: [],
          text_encoders: [],
          replacements: REPLACEMENTS[url.searchParams.get("slot_kind")] || [],
          replacements_reason: null,
          replacements_narrowed: true,
        });
      }
      if (rest === "/save" && method === "POST") {
        const body = request.postDataJSON();
        if (body.dry_run) {
          net.dryRuns.push(body);
          return json(route, {
            workflow_id: own,
            version: now.changes.version,
            dry_run: true,
            ...now.dryRun,
          });
        }
        net.saves.push(body);
        if (net.hold.save) await net.hold.save.promise;
        if (now.saveRefusal) return json(route, { detail: now.saveRefusal }, 409);
        // The workflow now holds what was saved: later reads answer for it.
        if (now.afterSave) Object.assign(now, now.afterSave);
        return json(route, {
          workflow_id: own,
          version: 4,
          new_workflow: false,
          left_out: [],
          ...now.saveExtra,
        });
      }
      if (rest === "/save-as-new" && method === "POST") {
        const body = request.postDataJSON();
        net.savesAsNew.push(body);
        if (net.hold.saveAsNew) await net.hold.saveAsNew.promise;
        if (now.saveAsNewRefusal) return json(route, { detail: now.saveAsNewRefusal }, 409);
        // Any save, this one too, drops what the run changed (the server's
        // `clear_changes`): the offer for it must not come back.
        if (now.afterSave) Object.assign(now, now.afterSave);
        return json(
          route,
          {
            workflow_id: NEW_WF,
            name: body.name,
            new_workflow: true,
            version: 1,
            left_out: [],
            ...now.saveExtra,
          },
          201,
        );
      }

      net.unstubbed.push(`${method} ${path}${url.search}`);
      return json(route, { detail: "not stubbed by run-popup-save.spec.js" }, 599);
    },
  );
  return net;
}

// "Create with LoRA" is the same popup, opened from a person or set.
const dialog = (page) =>
  page.locator(".app-dialog").filter({ hasText: /Run workflow|Create with LoRA/ });
const band = (page) => dialog(page).getByTestId("rund-band");
const triggerLine = (page) => dialog(page).getByTestId("rund-trigger");
const tile = (page, name) =>
  dialog(page).getByRole("group", { name: "Person" }).getByRole("checkbox", { name });
const personRows = (page) => dialog(page).getByRole("combobox", { name: /'s LoRA$/ });
const promptBox = (page) => dialog(page).locator(".app-textarea textarea").first();
const runButton = (page) => dialog(page).locator("footer").getByRole("button", { name: /^Run/ });

/** Select the stubbed workflow on the Workflows screen. */
async function selectWorkflow(page) {
  await page.goto("/workflows");
  const row = page.locator(".wfv-row").first();
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.click();
  await expect(page.getByRole("complementary", { name: "Inspector" })).toBeVisible();
}

/** Open the Run popup the way the owner does: the card, then Run…. */
async function openPopup(page) {
  await selectWorkflow(page);
  await page
    .getByRole("complementary", { name: "Inspector" })
    .getByRole("button", { name: /^Run( with changes)?…$/ })
    .click();
  await expect(dialog(page)).toBeVisible();
  // Settled: the dialog scales in, and a box measured on the way is 0.9 of
  // itself.
  await expect(
    page.locator(".v-overlay__content").filter({ has: dialog(page) }),
  ).not.toHaveClass(/dialog-transition/);
  // Loaded: the form is drawn, with the workflow's own prompt in its box.
  await expect(promptBox(page)).toHaveValue(/portrait by a tall window/);
}

async function shot(locator, name) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await locator.screenshot({ path: join(SHOTS, `${name}.png`) });
}

/** The top of the rail, down to the foot of *last*: the tabs and the offer. */
async function shotRail(page, last, name) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  const rail = await page.getByRole("complementary", { name: "Inspector" }).boundingBox();
  const foot = await last.boundingBox();
  await page.screenshot({
    path: join(SHOTS, `${name}.png`),
    clip: { x: rail.x, y: rail.y, width: rail.width, height: foot.y + foot.height + 16 - rail.y },
  });
}

/** Whether *locator*'s content is wider than its box. */
const overflows = (locator) =>
  locator.evaluate((el) => el.scrollWidth > el.clientWidth);
const heightOf = async (locator) => (await locator.boundingBox()).height;

/** A promise a test releases by hand: what a route holds its answer on. */
function gate() {
  let release;
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

let net;
test.afterEach(() => {
  // Loud, and last: a request that fell through would have been answered 599.
  expect(net?.unstubbed ?? [], "workflow routes this spec did not stub").toEqual([]);
});

/** The three changes the design's own picture shows. */
async function changeThree(page) {
  await dialog(page).getByRole("combobox", { name: "Checkpoint" }).selectOption("juggernautXL_v9.safetensors");
  await dialog(page).getByRole("button", { name: "Remove film-grain-35mm" }).click();
  // The × went with its row's controls: the keyboard is on the way back.
  await expect(
    dialog(page).getByRole("button", { name: "Put film-grain-35mm back" }),
  ).toBeFocused();
  await dialog(page).getByRole("spinbutton", { name: "Steps" }).fill("32");
  await expect(band(page)).toContainText("3 changes from the workflow");
}

const THREE_CHANGES = {
  models: [{ address: CKPT, filename: "juggernautXL_v9.safetensors" }],
  loras: [],
  skip_loras: [{ node_id: "8", field: "lora_name" }],
  stages: {},
  values: [{ slot_label: "KSampler", input_name: "steps", value: 32 }],
};

test.describe("several people in a run", () => {
  test("adds a row per person in the order picked, and a tile pressed again takes its row away", async ({
    page,
  }) => {
    net = await stubWorkflow(page);
    await openPopup(page);
    await expect(tile(page, "No one")).toBeChecked();

    // Bea first, though Ada is drawn first.
    await tile(page, "Bea").click();
    await tile(page, "Ada").click();
    await expect(tile(page, "Bea")).toBeChecked();
    await expect(tile(page, "Ada")).toBeChecked();
    await expect(tile(page, "No one")).not.toBeChecked();
    await expect(personRows(page)).toHaveCount(2);
    expect(
      await personRows(page).evaluateAll((rows) => rows.map((row) => row.getAttribute("aria-label"))),
    ).toEqual(["Bea's LoRA", "Ada's LoRA"]);
    // Under the workflow's own loaders, in the one LoRA list.
    const order = await dialog(page)
      .locator(".rund-lora select")
      .evaluateAll((rows) => rows.map((row) => row.selectedOptions[0]?.textContent.trim()));
    expect(order).toEqual(["detail_slider_v2", "film-grain-35mm", "bea_sdxl", "ada_sdxl"]);

    await tile(page, "Bea").click();
    await expect(tile(page, "Bea")).not.toBeChecked();
    await expect(personRows(page)).toHaveCount(1);
    await expect(personRows(page)).toHaveAccessibleName("Ada's LoRA");
    // The tile keeps the keyboard: it is where the pick is taken back.
    await expect(tile(page, "Bea")).toBeFocused();
  });

  test("says a missing trigger word on one row, with a button per word named for whose it is", async ({
    page,
  }) => {
    net = await stubWorkflow(page);
    await openPopup(page);
    await tile(page, "Bea").click();
    await tile(page, "Ada").click();

    const line = triggerLine(page);
    await expect(line).toContainText("Trigger word missing");
    const bea = line.getByRole("button", {
      name: "Add beatrix-kiddo-character-v2, Bea's trigger word",
    });
    const ada = line.getByRole("button", {
      name: "Add ada lovelace portrait woman, Ada's trigger word",
    });
    await expect(bea).toBeVisible();
    await expect(ada).toBeVisible();
    await expect(line.getByRole("button")).toHaveCount(2);

    // One row: the line is as tall as one of its buttons, and nothing in it
    // or in the dialog is wider than its box.
    expect(await heightOf(line)).toBe(await heightOf(bea));
    expect(await overflows(line)).toBe(false);
    expect(await overflows(dialog(page).locator(".app-dialog__body"))).toBe(false);
    // The words do not shrink; the buttons do, each to an ellipsis.
    expect(await overflows(line.locator(".rund-trigger-text"))).toBe(false);
    expect(await overflows(ada.locator(".app-btn__label"))).toBe(true);
    await shot(dialog(page), "1-two-people-trigger-line");

    await ada.click();
    await expect(promptBox(page)).toBeFocused();
    await expect(promptBox(page)).toHaveValue(
      "ada lovelace portrait woman, portrait by a tall window, soft overcast light",
    );
    await expect(line.getByRole("button")).toHaveCount(1);
    await bea.click();
    await expect(promptBox(page)).toBeFocused();
    await expect(line).toHaveCount(0);
  });

  test("offers one button for all of them past three missing words", async ({ page }) => {
    net = await stubWorkflow(page, { people: PEOPLE });
    await openPopup(page);
    for (const name of ["Ada", "Bea", "Cyd"]) await tile(page, name).click();
    const line = triggerLine(page);
    await expect(line.getByRole("button")).toHaveCount(3);
    expect(await heightOf(line)).toBe(await heightOf(line.getByRole("button").first()));
    expect(await overflows(line)).toBe(false);

    await tile(page, "Dot").click();
    const all = line.getByRole("button", {
      name: "Add all 4 missing trigger words: ada lovelace portrait woman, beatrix-kiddo-character-v2, cydonia the red planet girl, dotty",
    });
    await expect(all).toHaveText("Add all 4");
    await expect(line.getByRole("button")).toHaveCount(1);
    await all.click();
    await expect(promptBox(page)).toBeFocused();
    await expect(promptBox(page)).toHaveValue(
      /^ada lovelace portrait woman, beatrix-kiddo-character-v2, cydonia the red planet girl, dotty, portrait by/,
    );
    await expect(line).toHaveCount(0);
  });
});

test.describe("at the desktop window's narrowest", () => {
  // `minWidth: 800` in electron/src/main.ts: the popup is 720 wide there too.
  test.use({ viewport: { width: 800, height: 600 } });

  test("neither the trigger line nor the band wraps or overflows", async ({ page }) => {
    net = await stubWorkflow(page, { people: PEOPLE });
    await openPopup(page);
    expect((await dialog(page).boundingBox()).width).toBe(720);
    for (const name of ["Ada", "Bea", "Cyd"]) await tile(page, name).click();
    const line = triggerLine(page);
    await expect(line.getByRole("button")).toHaveCount(3);
    expect(await heightOf(line)).toBe(await heightOf(line.getByRole("button").first()));
    expect(await overflows(line)).toBe(false);

    // A long list of changes: every kind the band names.
    await changeThree(page);
    await dialog(page).getByRole("combobox", { name: "VAE" }).selectOption("ae.safetensors");
    await dialog(page).getByRole("spinbutton", { name: "Strength of LoRA 1" }).fill("0.9");
    await dialog(page).getByRole("spinbutton", { name: "Width" }).fill("1024");
    await dialog(page).getByRole("spinbutton", { name: "CFG" }).fill("4");
    await dialog(page).getByRole("checkbox", { name: "Upscale" }).uncheck();
    await dialog(page).getByRole("checkbox", { name: "Face detailer" }).check();
    await expect(band(page)).toContainText("9 changes from the workflow");
    const list = band(page).locator(".rund-band-list");
    await expect(list).toHaveText(
      "Checkpoint, VAE, 1 LoRA changed, 1 LoRA removed, Upscale off, Face detailer on, Steps, CFG, Size.",
    );
    // The list is cut on its one line; what stays with the run is whole.
    expect(await overflows(list)).toBe(true);
    expect(await heightOf(list)).toBeLessThan(24);
    expect(await overflows(band(page).locator(".rund-band-stays"))).toBe(false);
    // Text and both verbs on one row, inside the band, inside the dialog.
    const save = band(page).getByRole("button", { name: "Save", exact: true });
    const asNew = band(page).getByRole("button", { name: "Save as new…" });
    expect((await save.boundingBox()).y).toBe((await asNew.boundingBox()).y);
    expect(await overflows(band(page))).toBe(false);
    expect(await overflows(dialog(page).locator(".app-dialog__body"))).toBe(false);
    // And readable at rest: the form is taller than this window, and the band
    // is on screen all the same, above the footer.
    await expect(band(page)).toBeInViewport({ ratio: 1 });
    const footer = await dialog(page).locator("footer").boundingBox();
    const bandBox = await band(page).boundingBox();
    expect(bandBox.y + bandBox.height).toBeLessThanOrEqual(footer.y);
    await shot(dialog(page), "3-band-at-800-wide");
  });
});

test.describe("in a window tall enough for the whole form", () => {
  // Where the dialog does not scroll, the band is what makes it taller: that
  // is the one case in which the footer can move under the pointer at all.
  test.use({ viewport: { width: 1280, height: 1500 } });

  test("is absent on a plain run, and leaves Run where it was once the change is put back", async ({
    page,
  }) => {
    net = await stubWorkflow(page);
    await openPopup(page);
    await expect(band(page)).toHaveCount(0);
    const body = dialog(page).locator(".app-dialog__body");
    expect(await body.evaluate((el) => el.scrollHeight <= el.clientHeight)).toBe(true);
    const plain = await runButton(page).boundingBox();
    const plainDialog = await dialog(page).boundingBox();

    const steps = dialog(page).getByRole("spinbutton", { name: "Steps" });
    await steps.fill("32");
    await expect(band(page)).toContainText("1 change from the workflow");
    // The dialog grew by the band and the one gap above it, and by nothing
    // else, so Run moved under the pointer by no more than that.
    const grew = (await dialog(page).boundingBox()).height - plainDialog.height;
    const bandAndGap = (await heightOf(band(page))) + 16;
    expect(Math.abs(grew - bandAndGap)).toBeLessThanOrEqual(1);
    const withBand = await runButton(page).boundingBox();
    expect(withBand.y).not.toBe(plain.y);
    expect(Math.abs(withBand.y - plain.y)).toBeLessThanOrEqual(bandAndGap);

    await dialog(page).getByRole("button", { name: "Put Steps back to 28" }).click();
    await expect(band(page)).toHaveCount(0);
    expect(await runButton(page).boundingBox()).toEqual(plain);
    // Nothing was asked about saving, and nothing saved.
    expect(net.saves).toEqual([]);
  });
});

test.describe("the band", () => {
  test("counts and names three changes, with two verbs that are not the amber one", async ({
    page,
  }) => {
    net = await stubWorkflow(page);
    await openPopup(page);
    await changeThree(page);
    await expect(band(page).locator(".rund-band-list")).toHaveText(
      "Checkpoint, 1 LoRA removed, Steps.",
    );
    await expect(band(page).locator(".rund-band-stays")).toHaveText(
      "Prompt, seed, count and the people picked stay with the run.",
    );
    // The removed loader stays, struck through, with the way back.
    const removed = dialog(page).locator(".rund-lora--skipped");
    await expect(removed).toContainText("film-grain-35mm");
    await expect(removed).toContainText("Removed");
    await expect(removed.getByRole("button", { name: "Put film-grain-35mm back" })).toBeVisible();

    const fill = (locator) => locator.evaluate((el) => getComputedStyle(el).backgroundColor);
    const amber = await fill(runButton(page));
    const save = band(page).getByRole("button", { name: "Save", exact: true });
    const asNew = band(page).getByRole("button", { name: "Save as new…" });
    expect(await fill(save)).not.toBe(amber);
    expect(await fill(asNew)).not.toBe(amber);
    expect(await fill(save)).toBe(await fill(asNew));
    // At the small size, under the footer's own.
    expect(await heightOf(save)).toBeLessThan(await heightOf(runButton(page)));
    // On screen without scrolling the form, above the footer: the form is
    // taller than the window and scrolls under the band, which holds.
    await expect(band(page)).toBeInViewport({ ratio: 1 });
    const body = dialog(page).locator(".app-dialog__body");
    expect(await body.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
    const held = await band(page).boundingBox();
    await body.evaluate((el) => el.scrollTo(0, 0));
    expect((await band(page).boundingBox()).y).toBe(held.y);
    // And nothing of the form shows in the strip between it and the footer:
    // the band paints that strip in the dialog's own surface.
    const strip = await page.evaluate(() => {
      const el = document.querySelector('[data-testid="rund-band"]');
      const paint = getComputedStyle(el, "::after");
      const foot = document.querySelector(".app-dialog__footer").getBoundingClientRect();
      return {
        gap: foot.top - el.getBoundingClientRect().bottom,
        painted: parseFloat(paint.height) || 0,
        fill: paint.backgroundColor,
        surface: getComputedStyle(document.querySelector(".app-dialog")).backgroundColor,
      };
    });
    expect(strip.gap).toBeGreaterThan(0);
    expect(strip.painted).toBeGreaterThanOrEqual(strip.gap);
    expect(strip.fill).toBe(strip.surface);
    await shot(dialog(page), "2-band-at-1280-wide");
  });

  test("prints the server's reason for a closed Save, which neither a click nor Enter gets past", async ({
    page,
  }) => {
    const reason =
      "This changes the checkpoint, which makes it another workflow. Save it as a new one.";
    net = await stubWorkflow(page, {
      dryRun: { save: { open: false, reason }, save_as_new: OPEN },
    });
    await openPopup(page);
    await changeThree(page);
    const why = band(page).getByTestId("rund-band-why");
    await expect(why).toHaveText(reason);
    await expect(why).toBeInViewport();
    // Asked as a dry run of the very save: the three changes and the version.
    expect(net.dryRuns.at(-1)).toEqual({ changes: THREE_CHANGES, version: 3, dry_run: true });

    const save = band(page).getByRole("button", { name: "Save", exact: true });
    await expect(save).toHaveAttribute("aria-disabled", "true");
    await expect(save).toHaveAccessibleDescription(reason);
    // Pressed for real, three ways. `force`: Playwright itself will not click
    // what says it is disabled, and a person can.
    await save.click({ force: true });
    await save.focus();
    await expect(save).toBeFocused();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Space");
    // The other verb is open and says so by being pressable.
    await expect(band(page).getByRole("button", { name: "Save as new…" })).toBeEnabled();
    await shot(dialog(page), "5-band-save-closed");
    // A dry run that follows would have been recorded after any save.
    await band(page).getByRole("button", { name: "Save as new…" }).click();
    await expect(dialog(page).getByRole("textbox", { name: "Name of the new workflow" })).toBeFocused();
    expect(net.saves).toEqual([]);
  });

  test("turns into its one question for Save as new…, and Escape or Back gives it back", async ({
    page,
  }) => {
    net = await stubWorkflow(page);
    await openPopup(page);
    await changeThree(page);
    const asNew = band(page).getByRole("button", { name: "Save as new…" });
    const name = dialog(page).getByRole("textbox", { name: "Name of the new workflow" });

    await asNew.click();
    await expect(name).toBeFocused();
    await expect(name).toHaveValue("Portrait, soft window light (Juggernaut XL v9)");
    await expect(band(page).getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
    // The buttons sit on the field's own line, not across its label.
    const field = await name.boundingBox();
    const back = await band(page).getByRole("button", { name: "Back" }).boundingBox();
    expect(back.y).toBeGreaterThanOrEqual(field.y);
    expect(back.y + back.height).toBeLessThanOrEqual(field.y + field.height + 1);
    await shot(dialog(page), "4-band-name-question");

    await page.keyboard.press("Escape");
    // Back to the band, not out of the popup.
    await expect(name).toHaveCount(0);
    await expect(dialog(page)).toBeVisible();
    await expect(asNew).toBeFocused();

    await asNew.click();
    await expect(name).toBeFocused();
    await band(page).getByRole("button", { name: "Back" }).click();
    await expect(name).toHaveCount(0);
    await expect(asNew).toBeFocused();
    expect(net.savesAsNew).toEqual([]);

    // And it does save, under the name typed, and moves onto the new workflow.
    await asNew.click();
    await name.fill("Portrait on Juggernaut");
    await page.keyboard.press("Enter");
    await expect(dialog(page).getByTestId("rund-band-done")).toHaveText(
      "Saved as Portrait on Juggernaut.",
    );
    expect(net.savesAsNew).toEqual([{ name: "Portrait on Juggernaut", changes: THREE_CHANGES }]);
    expect(net.saves).toEqual([]);
  });
});

test.describe("Save", () => {
  test("sends exactly what the band names, then clears the chips and stays open", async ({
    page,
  }) => {
    net = await stubWorkflow(page, {
      // What the workflow holds once the save has landed.
      afterSave: {
        card: workflowCard({ checkpoint: "juggernautXL_v9.safetensors", steps: 32 }),
        chain: [CHAIN[0]],
        changes: changesState({ version: 4 }),
      },
    });
    await openPopup(page);
    await promptBox(page).fill("a portrait typed for this run");
    await changeThree(page);
    await expect(dialog(page).locator(".reset-chip")).not.toHaveCount(0);

    await band(page).getByRole("button", { name: "Save", exact: true }).click();
    const done = dialog(page).getByTestId("rund-band-done");
    await expect(done).toHaveText("Saved to this workflow.");
    await expect(done).toBeFocused();
    await expect(done).toBeInViewport();
    expect(net.saves).toEqual([{ changes: THREE_CHANGES, version: 3 }]);
    expect(net.savesAsNew).toEqual([]);

    // The workflow's values are now what the form shows: only the prompt,
    // which is the run's, still carries a chip.
    await expect(band(page)).toHaveCount(0);
    await expect(dialog(page).locator(".reset-chip")).toHaveCount(1);
    await expect(dialog(page).getByRole("button", { name: /^Put Prompt back/ })).toBeVisible();
    await expect(dialog(page).getByRole("spinbutton", { name: "Steps" })).toHaveValue("32");
    await expect(dialog(page).locator(".rund-lora--skipped")).toHaveCount(0);
    // Still open, on the same workflow, with what was typed for the run.
    await expect(dialog(page)).toBeVisible();
    await expect(dialog(page).locator(".app-dialog__subtitle")).toHaveText("Portrait, soft window light");
    await expect(promptBox(page)).toHaveValue("a portrait typed for this run");
    expect(net.waitingWrites).toEqual([]);
  });

  test("is never sent by Run", async ({ page }) => {
    net = await stubWorkflow(page);
    await openPopup(page);
    await changeThree(page);
    await runButton(page).click();
    await expect(dialog(page)).toHaveCount(0);
    expect(net.runs).toHaveLength(1);
    expect(net.runs[0].skip_loras).toEqual(THREE_CHANGES.skip_loras);
    expect(net.saves).toEqual([]);
    expect(net.savesAsNew).toEqual([]);
    expect(net.waitingWrites).toEqual([]);
  });

  test("is never sent by closing", async ({ page }) => {
    net = await stubWorkflow(page);
    await openPopup(page);
    await changeThree(page);
    await dialog(page).locator("footer").getByRole("button", { name: "Cancel" }).click();
    await expect(dialog(page)).toHaveCount(0);
    expect(net.saves).toEqual([]);
    expect(net.savesAsNew).toEqual([]);
    expect(net.waitingWrites).toEqual([]);
    expect(net.runs).toEqual([]);
  });
});

test("opens on what waits in the Workflow tab, and a change put back is restated at save", async ({
  page,
}) => {
  const waiting = {
    models: [{ address: CKPT, filename: "juggernautXL_v9.safetensors" }],
    loras: [],
    skip_loras: [{ node_id: "8", field: "lora_name" }],
    stages: {},
    values: [{ slot_label: "KSampler", input_name: "steps", value: 32 }],
  };
  net = await stubWorkflow(page, {
    changes: changesState({ waiting, count: 3, version: 6, save: OPEN }),
    // A save clears what waited, and the workflow then holds it.
    afterSave: {
      card: workflowCard({ checkpoint: "juggernautXL_v9.safetensors", steps: 32 }),
      changes: changesState({ version: 7 }),
    },
  });
  await openPopup(page);

  // Laid over the form as the owner's own edits, shown against what is SAVED.
  await expect(band(page)).toContainText("3 changes from the workflow");
  await expect(band(page).locator(".rund-band-list")).toHaveText("Checkpoint, 1 LoRA removed, Steps.");
  await expect(dialog(page).getByRole("combobox", { name: "Checkpoint" })).toHaveValue(
    "juggernautXL_v9.safetensors",
  );
  await expect(dialog(page).getByRole("spinbutton", { name: "Steps" })).toHaveValue("32");
  await expect(dialog(page).getByRole("button", { name: "Put Steps back to 28" })).toBeVisible();
  await expect(dialog(page).getByRole("button", { name: "Put Checkpoint back to realvisXL v5" })).toBeVisible();
  await expect(dialog(page).locator(".rund-lora--skipped")).toContainText("film-grain-35mm");

  // The loader put back here: the server would still save the waiting
  // removal unless this save says the loader is set.
  await dialog(page).getByRole("button", { name: "Put film-grain-35mm back" }).click();
  await expect(band(page)).toContainText("2 changes from the workflow");
  await band(page).getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog(page).getByTestId("rund-band-done")).toBeVisible();
  expect(net.saves).toEqual([
    {
      changes: {
        models: waiting.models,
        // With its clip strength, which the graph has as the same number.
        loras: [
          {
            node_id: "8",
            field: "lora_name",
            sha256: GRAIN_SHA,
            strength_model: 0.4,
            strength_clip: 0.4,
          },
        ],
        skip_loras: [],
        stages: {},
        values: waiting.values,
      },
      version: 6,
    },
  ]);
  // What waits is the Workflow tab's: the popup never writes it.
  expect(net.waitingWrites).toEqual([]);
});

test.describe("from the keyboard", () => {
  test("the band's two verbs are its only tab stops, in reading order", async ({ page }) => {
    net = await stubWorkflow(page);
    await openPopup(page);
    await changeThree(page);
    const asNew = band(page).getByRole("button", { name: "Save as new…" });
    const save = band(page).getByRole("button", { name: "Save", exact: true });
    await asNew.focus();
    await page.keyboard.press("Tab");
    await expect(save).toBeFocused();
    await page.keyboard.press("Tab");
    // Out of the band, into the footer.
    await expect(dialog(page).locator("footer").getByRole("button", { name: "Save as recipe" })).toBeFocused();
    await asNew.focus();
    await page.keyboard.press("Shift+Tab");
    const inBand = await band(page).evaluate((el) => el.contains(document.activeElement));
    expect(inBand).toBe(false);
    await expect(dialog(page).locator(":focus")).toHaveCount(1);
  });

  test("a model row is picked from the keyboard and keeps the focus", async ({ page }) => {
    net = await stubWorkflow(page);
    await openPopup(page);
    const vae = dialog(page).getByRole("combobox", { name: "VAE" });
    // By the names the shelf gives them, never a file with its extension.
    expect(await vae.locator("option").allTextContents().then((all) => all.map((t) => t.trim()))).toEqual([
      "SDXL VAE",
      "Flux AE",
    ]);
    await vae.focus();
    await page.keyboard.press("ArrowDown");
    await expect(vae).toHaveValue("ae.safetensors");
    await expect(vae).toBeFocused();
    await expect(band(page).locator(".rund-band-list")).toHaveText("VAE.");
    await expect(dialog(page).getByRole("button", { name: "Put VAE back to sdxl vae" })).toBeVisible();
    await page.keyboard.press("ArrowUp");
    await expect(vae).toHaveValue("sdxl_vae.safetensors");
    await expect(vae).toBeFocused();
    await expect(band(page)).toHaveCount(0);
  });
});

test.describe("the Tasks tab's offer", () => {
  const RAN = THREE_CHANGES;
  const rail = (page) => page.getByTestId("titlebar-tasks-panel");
  const pill = (page) => page.getByTestId("titlebar-tasks");
  const offer = (page) => rail(page).locator("[data-offer]");

  async function openTasks(page) {
    await selectWorkflow(page);
    await pill(page).click();
    // After a reload the run comes back folded; its pill says why to open it.
    await expect(rail(page).getByTestId("tm-finished-pill")).toHaveText("3 changes");
    await rail(page).getByRole("button", { name: `Show the run of ${FINISHED.label}` }).click();
  }

  test("is drawn from the server's `ran` after a reload, with a count alone, inside the rail", async ({
    page,
  }) => {
    net = await stubWorkflow(page, {
      changes: changesState({ ran: RAN, ran_save: OPEN }),
      finished: [FINISHED],
    });
    await openTasks(page);
    await expect(offer(page)).toContainText("Portrait, soft window light");
    await expect(offer(page)).toContainText("with 3 changes from the workflow.");
    await expect(offer(page).getByRole("group", { name: "Pictures the run made" })).toBeVisible();
    // Not an active task, and the panel is not empty either.
    await expect(rail(page)).not.toContainText("No active tasks");
    await expect(offer(page).getByRole("button", { name: "Save as new…" })).toBeVisible();
    await expect(offer(page).getByRole("button", { name: "Save", exact: true })).toBeVisible();

    // The panel is narrower than the window: the row and its two verbs fit it.
    expect(await overflows(offer(page))).toBe(false);
    expect(await overflows(rail(page).locator(".ttp-body"))).toBe(false);
    const row = await offer(page).boundingBox();
    for (const name of ["Save as new…", "Save"]) {
      const box = await offer(page).getByRole("button", { name, exact: true }).boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(row.x);
      expect(box.x + box.width).toBeLessThanOrEqual(row.x + row.width);
    }
    await shotRail(page, offer(page), "6-tasks-tab-offer");
  });

  test("offers nothing where the last run changed nothing", async ({ page }) => {
    net = await stubWorkflow(page, { finished: [FINISHED] });
    await selectWorkflow(page);
    await pill(page).click();
    // Listed, as a receipt: no pill, and opened, no verbs.
    const run = rail(page).getByTestId("tm-finished-run");
    await expect(run).toContainText(FINISHED.label);
    await rail(page).getByRole("button", { name: `Show the run of ${FINISHED.label}` }).click();
    await expect(run).toContainText("Finished just now.");
    await expect(rail(page).getByTestId("tm-finished-pill")).toHaveCount(0);
    await expect(offer(page)).toHaveCount(0);
  });

  test("saves what the run was made with, and says so where the row was", async ({ page }) => {
    net = await stubWorkflow(page, {
      changes: changesState({ ran: RAN, ran_save: OPEN }),
      afterSave: { changes: changesState({ version: 4 }) },
      finished: [FINISHED],
    });
    await openTasks(page);
    await offer(page).getByRole("button", { name: "Save", exact: true }).click();
    const done = rail(page).getByTestId("tm-offer-done");
    await expect(done).toHaveText("Saved to Portrait, soft window light.");
    await expect(done).toBeFocused();
    await expect(offer(page)).toHaveCount(0);
    expect(net.saves).toEqual([{ changes: RAN, version: 3, from_run: true }]);
  });

  test("asks for a name for Save as new…, and stores the run's changes under it", async ({
    page,
  }) => {
    net = await stubWorkflow(page, {
      changes: changesState({ ran: RAN, ran_save: OPEN }),
      // The save drops `ran`, as the server does: an offer that came back
      // after the receipt would take the receipt down.
      afterSave: { changes: changesState() },
      finished: [FINISHED],
    });
    await openTasks(page);
    const asNew = offer(page).getByRole("button", { name: "Save as new…" });
    const name = offer(page).getByRole("textbox", { name: "Name of the new workflow" });
    await asNew.click();
    await expect(name).toBeFocused();
    await expect(name).toHaveValue("Portrait, soft window light 2");
    expect(await overflows(offer(page))).toBe(false);
    await page.keyboard.press("Escape");
    await expect(name).toHaveCount(0);
    await expect(asNew).toBeFocused();

    await asNew.click();
    await name.fill("Portrait on Juggernaut");
    await offer(page).getByRole("button", { name: "Save as new workflow" }).click();
    await expect(rail(page).getByTestId("tm-offer-done")).toHaveText(
      "Saved as Portrait on Juggernaut.",
    );
    expect(net.savesAsNew).toEqual([
      { name: "Portrait on Juggernaut", changes: RAN, from_run: true },
    ]);
    expect(net.saves).toEqual([]);
  });

  test("prints the server's reason for a closed Save, which cannot be pressed", async ({
    page,
  }) => {
    const reason =
      "This changes the checkpoint, which makes it another workflow. Save it as a new one.";
    net = await stubWorkflow(page, {
      changes: changesState({ ran: RAN, ran_save: { open: false, reason } }),
      finished: [FINISHED],
    });
    await openTasks(page);
    await expect(offer(page)).toContainText(reason);
    const save = offer(page).getByRole("button", { name: "Save", exact: true });
    await expect(save).toHaveAttribute("aria-disabled", "true");
    await expect(save).toHaveAccessibleDescription(reason);
    await save.click({ force: true });
    await save.focus();
    await expect(save).toBeFocused();
    // Space, not Enter: the popover's own Enter handling (Vuetify's VMenu,
    // closeOnContentClick off) moves focus on past the last control and
    // closes the menu, so Enter never reaches the button.
    await page.keyboard.press("Space");
    await expect(save).toBeFocused();
    // The reason wraps inside the rail; nothing is pushed out of it.
    expect(await overflows(offer(page))).toBe(false);
    await shotRail(page, offer(page), "7-tasks-tab-offer-save-closed");
    // The other verb still answers, which is what shows the page is live.
    await offer(page).getByRole("button", { name: "Save as new…" }).click();
    await expect(offer(page).getByRole("textbox", { name: "Name of the new workflow" })).toBeFocused();
    expect(net.saves).toEqual([]);
  });
});

// ── What only a browser shows: a save in flight, and who has the keyboard ───

test.describe("a band save that is slow", () => {
  const AFTER = () => ({
    afterSave: {
      card: workflowCard({ checkpoint: "juggernautXL_v9.safetensors", steps: 32 }),
      chain: [CHAIN[0]],
      changes: changesState({ version: 4 }),
    },
  });

  test("the form cannot be edited, Run and Cancel and Escape do nothing, and the line it ends on has the keyboard", async ({
    page,
  }) => {
    net = await stubWorkflow(page, AFTER());
    await openPopup(page);
    await changeThree(page);
    const steps = dialog(page).getByRole("spinbutton", { name: "Steps" });
    await expect(steps).toHaveValue("32");

    net.hold.save = gate();
    await band(page).getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => net.saves.length).toBe(1);

    // The form is inert and Run says why: its wait sentence, in the footer.
    const wait = dialog(page).locator('footer p[tabindex="-1"]');
    await expect(wait).toHaveText("Run waits for the save to finish.");
    await expect(dialog(page).locator(".rund")).toHaveAttribute("inert", "");
    // A pointer on the field and keys typed: neither reaches it.
    await steps.click({ force: true });
    await page.keyboard.type("9");
    await expect(steps).toHaveValue("32");

    // Run is inert: pressed (a person can press an aria-disabled button), it
    // sends nothing and the popup stays.
    await expect(runButton(page)).toHaveAttribute("aria-disabled", "true");
    await runButton(page).click({ force: true });
    // Cancel and Escape do not close it.
    await expect(dialog(page).locator("footer").getByRole("button", { name: "Cancel" })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(dialog(page)).toBeVisible();
    expect(net.runs).toEqual([]);
    expect(net.saves).toHaveLength(1);

    net.hold.save.release();
    const done = dialog(page).getByTestId("rund-band-done");
    await expect(done).toHaveText("Saved to this workflow.");
    await expect(done).toBeFocused();
    await expect(wait).toHaveCount(0);
    await expect(dialog(page).locator(".rund")).not.toHaveAttribute("inert", "");
    await expect(steps).toHaveValue("32");
    // Only the one save went out, and Run is live again.
    expect(net.saves).toHaveLength(1);
    expect(net.runs).toEqual([]);
    await expect(runButton(page)).not.toHaveAttribute("aria-disabled", "true");
  });

  test("Save as new… sent with Enter and refused leaves the keyboard in the name, beside the refusal", async ({
    page,
  }) => {
    const refusal = "A workflow with that name is already kept. Pick another name.";
    net = await stubWorkflow(page, { saveAsNewRefusal: refusal });
    await openPopup(page);
    await changeThree(page);
    await band(page).getByRole("button", { name: "Save as new…" }).click();
    const name = dialog(page).getByRole("textbox", { name: "Name of the new workflow" });
    await expect(name).toBeFocused();
    await name.fill("Taken");

    net.hold.saveAsNew = gate();
    await page.keyboard.press("Enter");
    await expect.poll(() => net.savesAsNew.length).toBe(1);
    // Out: read-only, not disabled, so it is still where the keyboard is.
    await expect(name).toHaveAttribute("readonly", "");
    await expect(name).toBeEnabled();
    await expect(name).toBeFocused();
    await expect(dialog(page).locator("footer p")).toHaveText("Run waits for the save to finish.");

    net.hold.saveAsNew.release();
    await expect(band(page).getByRole("alert")).toHaveText(refusal);
    await expect(name).toBeFocused();
    await expect(name).not.toHaveAttribute("readonly", "");
    await expect(name).toHaveValue("Taken");
    // Nothing moved: still this workflow, still its changes, no second send.
    await expect(dialog(page).locator(".app-dialog__subtitle")).toHaveText("Portrait, soft window light");
    // Still the question, with its changes behind it, and one send only.
    await band(page).getByRole("button", { name: "Back" }).click();
    await expect(band(page)).toContainText("3 changes from the workflow");
    expect(net.savesAsNew).toHaveLength(1);
  });

  test("a click that leaves the caret in the form: the keyboard waits on the footer's sentence, never the page, and returns to the verb pressed after a refusal", async ({
    page,
  }) => {
    net = await stubWorkflow(page, { saveRefusal: "This workflow changed since you opened this." });
    await openPopup(page);
    await changeThree(page);
    const steps = dialog(page).getByRole("spinbutton", { name: "Steps" });
    await steps.focus();
    await expect(steps).toBeFocused();
    // Safari does not focus a button a click lands on. Chromium does, so the
    // press is made as Safari makes it: the mouse goes down on the verb and
    // the focus stays where it was.
    await page.evaluate(() =>
      document.addEventListener(
        "mousedown",
        (event) => {
          if (event.target.closest('[data-testid="rund-band"] button')) event.preventDefault();
        },
        true,
      ),
    );
    const save = band(page).getByRole("button", { name: "Save", exact: true });

    net.hold.save = gate();
    await save.click();
    await expect.poll(() => net.saves.length).toBe(1);
    const wait = dialog(page).locator('footer p[tabindex="-1"]');
    await expect(wait).toHaveText("Run waits for the save to finish.");
    // On the sentence, and so not on the page.
    await expect(wait).toBeFocused();
    expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);

    net.hold.save.release();
    await expect(band(page).getByRole("alert")).toHaveText("This workflow changed since you opened this.");
    await expect(save).toBeFocused();
    await expect(wait).toHaveCount(0);

    // And a save that lands, started the same way: the line it ends on.
    net.world.saveRefusal = "";
    net.hold.save = null;
    await steps.focus();
    await save.click();
    const done = dialog(page).getByTestId("rund-band-done");
    await expect(done).toHaveText("Saved to this workflow.");
    await expect(done).toBeFocused();
  });
});

test.describe("a run of a workflow by itself", () => {
  const WHOLE = { models: [], loras: [], skip_loras: [], stages: {}, values: [] };

  test("carries what waited as the popup read it, all five keys, whatever was put back here", async ({
    page,
  }) => {
    const waiting = {
      models: [],
      loras: [],
      skip_loras: [{ node_id: "8", field: "lora_name" }],
      stages: {},
      values: [{ slot_label: "KSampler", input_name: "steps", value: 32 }],
    };
    net = await stubWorkflow(page, {
      changes: changesState({ waiting, count: 2, version: 6, save: OPEN }),
    });
    await openPopup(page);
    // Put back here: the run no longer has it, and the save of it has to know
    // it had been seen.
    await dialog(page).getByRole("button", { name: "Put film-grain-35mm back" }).click();
    await runButton(page).click();
    await expect(dialog(page)).toHaveCount(0);
    expect(net.runs).toHaveLength(1);
    expect(net.runs[0].waited).toEqual(waiting);
    expect(net.runs[0].skip_loras ?? []).toEqual([]);
    expect(net.runs[0].workflow_id).toBe(WF);
  });

  test("carries the empty set where nothing waited, which is not the same as not saying", async ({
    page,
  }) => {
    net = await stubWorkflow(page);
    await openPopup(page);
    await runButton(page).click();
    await expect(dialog(page)).toHaveCount(0);
    expect(net.runs[0].waited).toEqual(WHOLE);
  });
});

test.describe("the picker moves to another workflow", () => {
  test("no band and no Run until the workflow arrived at is read", async ({ page }) => {
    net = await stubWorkflow(page, {
      comfyui: true,
      second: { name: "Landscape, golden hour" },
    });
    // A run with no picture behind it and the picker unset: a person's
    // "Create with LoRA…".
    await page.goto("/");
    await page.getByText("Ada", { exact: true }).click({ button: "right" });
    await page.getByRole("button", { name: "Create with LoRA…" }).click();
    await expect(dialog(page)).toBeVisible();
    const picker = dialog(page).getByRole("combobox", { name: "Workflow" });
    await picker.selectOption(WF);
    await expect(dialog(page).getByRole("spinbutton", { name: "Steps" })).toBeVisible();
    // A change on the first workflow: the band is up.
    await dialog(page).getByRole("spinbutton", { name: "Steps" }).fill("32");
    await expect(band(page)).toContainText("1 change from the workflow");

    // The card of the other is held on the wire.
    net.hold.card = gate();
    await picker.selectOption(OTHER_WF);
    const wait = dialog(page).locator("footer p");
    await expect(wait).toHaveText("Reading this workflow…");
    await expect(band(page)).toHaveCount(0);
    await expect(runButton(page)).toHaveAttribute("aria-disabled", "true");
    await expect(dialog(page).getByRole("button", { name: "Save as recipe" })).toBeDisabled();
    await runButton(page).click({ force: true });
    expect(net.runs).toEqual([]);
    await expect(dialog(page)).toBeVisible();

    net.hold.card.release();
    await expect(wait).toHaveCount(0);
    await expect(runButton(page)).not.toHaveAttribute("aria-disabled", "true");
    // Steps is a parameter this workflow has too, so the popup keeps what was
    // typed (it names what it could not keep), and the band is back, of the
    // workflow arrived at.
    await expect(dialog(page).getByRole("spinbutton", { name: "Steps" })).toHaveValue("32");
    await expect(band(page)).toContainText("1 change from the workflow");
    await runButton(page).click();
    await expect(dialog(page)).toHaveCount(0);
    expect(net.runs).toHaveLength(1);
    expect(net.runs[0].workflow_id).toBe(OTHER_WF);
  });
});

test.describe("the Tasks tab's receipt says what the save left", () => {
  const RAN = THREE_CHANGES;
  const rail = (page) => page.getByTestId("titlebar-tasks-panel");
  const pill = (page) => page.getByTestId("titlebar-tasks");
  const offer = (page) => rail(page).locator("[data-offer]");

  async function save(page, saveExtra) {
    net = await stubWorkflow(page, {
      changes: changesState({ ran: RAN, ran_save: OPEN }),
      afterSave: { changes: changesState({ version: 4 }) },
      saveExtra,
      finished: [FINISHED],
    });
    await selectWorkflow(page);
    await pill(page).click();
    await rail(page).getByRole("button", { name: `Show the run of ${FINISHED.label}` }).click();
    await offer(page).getByRole("button", { name: "Save", exact: true }).click();
    const done = rail(page).getByTestId("tm-offer-done");
    await expect(done).toContainText("Saved to Portrait, soft window light.");
    await expect(done).toBeFocused();
    return done;
  }

  test("a change still waiting is said under the receipt, inside the rail", async ({ page }) => {
    const done = await save(page, { still_waiting: 2 });
    await expect(done).toContainText(
      "Portrait, soft window light still has 2 changes waiting in the Workflow tab. They were not part of this save.",
    );
    // The receipt is one line of text for a reader and two for the eye.
    await expect(done).toHaveText(
      "Saved to Portrait, soft window light. Portrait, soft window light still has 2 changes waiting in the Workflow tab. They were not part of this save.",
    );
    expect(await overflows(done)).toBe(false);
    expect(await overflows(rail(page).locator(".ttp-body"))).toBe(false);
  });

  test("a change that could not be kept is said, in the warning colour", async ({ page }) => {
    const done = await save(page, {
      not_kept: {
        models: [],
        loras: [],
        skip_loras: [{ node_id: "8", field: "lora_name" }],
        stages: {},
        values: [],
      },
    });
    const lost = done.locator(".tm-offer-lost");
    await expect(lost).toHaveText(
      "Could not keep 1 change made after the run: 1 LoRA removed. The saved workflow no longer has what was changed, so this is gone: not saved, and not left waiting.",
    );
    await expect(done).not.toContainText("still has");
    expect(await overflows(done)).toBe(false);
    // Not the colour of the rest of the receipt.
    const colours = await done.evaluate((el) => [
      getComputedStyle(el).color,
      getComputedStyle(el.querySelector(".tm-offer-lost")).color,
    ]);
    expect(colours[1]).not.toBe(colours[0]);
  });
});
