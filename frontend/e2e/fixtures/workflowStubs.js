import { expect } from "./test.js";

// The Workflows screen and its Run popup, stubbed, for the specs that drive
// the popup (`run-popup-save`, `workflow-run`): one workflow, its people and
// LoRAs. The fixture library has no workflows and no ComfyUI, so every route
// the popup and the inspector behind it read is answered here. A workflow
// route nobody stubbed is recorded (`unstubbed`), so a spec can fail on it
// rather than pass on the real backend's 404.

export const WF = `auto:${"a".repeat(64)}`;
const NEW_WF = `manual:${"b".repeat(32)}`;
export const OTHER_WF = `auto:${"c".repeat(64)}`;
/** The workflow's last run, as the poll lists it under `finished` (#2050). */
export const FINISHED = {
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
export const CKPT = "core:CheckpointLoader/ckpt_name";
const VAE = "core:VAELoader/vae_name";
export const GRAIN_SHA = "1".repeat(64);
const DETAIL_SHA = "2".repeat(64);

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
export const PEOPLE = [
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
export function workflowCard({ checkpoint = "realvisXL_v5.safetensors", steps = 28 } = {}) {
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
export const CHAIN = [
  loader("7", "detail_slider_v2.safetensors", DETAIL_SHA, 0.6),
  loader("8", "loras/film-grain-35mm.safetensors", GRAIN_SHA, 0.4),
];

export const OPEN = { open: true, reason: null };
export const changesState = (overrides = {}) => ({
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
export async function stubWorkflow(page, world = {}) {
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
          without: null,
          cover_asset: null,
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
      return json(route, { detail: "not stubbed by workflowStubs.js" }, 599);
    },
  );
  return net;
}

// "Create with LoRA" is the same popup, opened from a person or set.
export const dialog = (page) =>
  page.locator(".app-dialog").filter({ hasText: /Run workflow|Create with LoRA/ });
export const band = (page) => dialog(page).getByTestId("rund-band");
export const triggerLine = (page) => dialog(page).getByTestId("rund-trigger");
export const tile = (page, name) =>
  dialog(page).getByRole("group", { name: "Person" }).getByRole("checkbox", { name });
export const personRows = (page) => dialog(page).getByRole("combobox", { name: /'s LoRA$/ });
export const promptBox = (page) => dialog(page).locator(".app-textarea textarea").first();
export const runButton = (page) => dialog(page).locator("footer").getByRole("button", { name: /^Run/ });

/** Select the stubbed workflow on the Workflows screen. */
export async function selectWorkflow(page) {
  await page.goto("/workflows");
  const row = page.locator(".wfv-row").first();
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.click();
  await expect(page.getByRole("complementary", { name: "Inspector" })).toBeVisible();
}

/** Open the Run popup the way the owner does: the card, then Run…. */
export async function openPopup(page) {
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

/** A promise a test releases by hand: what a route holds its answer on. */
export function gate() {
  let release;
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  return { promise, release };
}
