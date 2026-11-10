// The Workflow tab's fake server, for `workflow-tab-editor.spec.js`: two
// workflows, their versions, LoRA chain and waiting changes, answered from one
// small `world` that keeps what a write stored. A module of its own because
// `workflow-fakes-contract.spec.js` installs the same fake and holds its
// answers against the real server, so the fake cannot drift from the API
// while the spec that leans on it stays green.

export const WF = `auto:${"a".repeat(64)}`;
const OTHER = `auto:${"b".repeat(64)}`;
const NEW_WF = `manual:${"c".repeat(32)}`;

export const CKPT = "core:CheckpointLoaderSimple/ckpt_name";
export const VAE = "core:VAELoader/vae_name";
/** A name long enough to push a row past 288px if anything lets it. */
const CKPT_FILE =
  "SDXL/realvisXL_v50_lightning_bakedvae_fp16_pruned_emaonly.safetensors";
const CKPT_NAME = "realvisXL_v50_lightning_bakedvae_fp16_pruned_emaonly";
const VAE_FILE = "sdxl_vae.safetensors";

export const SHA = { detail: "1".repeat(64), grain: "2".repeat(64), light: "3".repeat(64) };

/** The clock every date in here is read against. */
export const NOW = "2026-10-05T12:00:00Z";

const def = (label, inputName, value, slotLabel = "KSampler") => ({
  label,
  slot_label: slotLabel,
  input_name: inputName,
  value,
  provenance: "best",
});

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
  loader("7", "detail_slider_v2.safetensors", SHA.detail, 0.6),
  loader("8", "loras/film-grain-35mm.safetensors", SHA.grain, 0.4),
  loader("9", "soft_light.safetensors", SHA.light, 0.8),
];

/** A workflow as `GET /workflows` and `GET /workflows/{id}` serve it. */
function workflowCard(id, name, overrides = {}) {
  return {
    id,
    name,
    type: "txt2img",
    type_label: "Text to Image",
    base_topology: `topology-${id.slice(-4)}`,
    topologies: [`topology-${id.slice(-4)}`],
    models: [
      { kind: "checkpoint", name: CKPT_NAME, filename: CKPT_FILE, slot_label: "CheckpointLoaderSimple" },
      { kind: "vae", name: "sdxl_vae", filename: VAE_FILE, slot_label: "VAELoader" },
    ],
    loras: [],
    picture_count: 184,
    rating: 4,
    rank: 1,
    covers: [],
    specials: ["upscale", "face_detailer"],
    defaults: [def("Steps", "steps", 28), def("CFG", "cfg", 5)],
    default_recipe: {
      sampled: 184,
      models: [
        { address: CKPT, kind: "checkpoint", filename: CKPT_FILE, provenance: "best" },
        { address: VAE, kind: "vae", filename: VAE_FILE, provenance: "best" },
      ],
      loras: CHAIN.map((entry) => ({
        asset: "",
        filename: entry.filename,
        sha256: entry.sha256,
        strength: entry.strength,
        provenance: "best",
      })),
      values: [def("Steps", "steps", 28), def("CFG", "cfg", 5)],
      stages: { upscale: true, face_detailer: false },
      stage_details: {},
    },
    ...overrides,
  };
}

const REPLACEMENTS = {
  checkpoint: [
    { id: 11, filename: "SDXL/juggernautXL_v9.safetensors", display_name: "Juggernaut XL v9" },
    { id: 12, filename: "SDXL/dreamshaperXL.safetensors", display_name: "DreamShaper XL" },
  ],
  vae: [{ id: 22, filename: "vae/sdxl-vae-fp16.safetensors", display_name: "SDXL VAE fp16" }],
};

/** `GET /models/workflow-sets`: two of the owner's own sets. */
const SHELF = {
  combinations: [],
  no_set: [],
  hand_made: [
    {
      id: 7,
      name: "Portraits",
      members: [
        { id: 11, slot: "checkpoint", on_shelf: true, name: "Juggernaut XL v9" },
        { id: 22, slot: "vae", on_shelf: true, name: "SDXL VAE fp16" },
      ],
    },
    {
      id: 8,
      name: "Flux dev",
      members: [{ id: 31, slot: "checkpoint", on_shelf: true, name: "flux dev" }],
    },
  ],
  set_checks: [],
};

export const OPEN = { open: true, reason: null };

const countOf = (set) =>
  (set?.models?.length ?? 0) +
  (set?.loras?.length ?? 0) +
  (set?.skip_loras?.length ?? 0) +
  Object.keys(set?.stages ?? {}).length +
  (set?.values?.length ?? 0);

/** Three versions of the first workflow, newest first as the route sends them. */
const VERSIONS = [
  { version: 3, created_at: "2026-10-02T12:00:00+00:00", reason: "models changed" },
  { version: 2, created_at: "2026-08-28T12:00:00+00:00", reason: "LoRAs edited" },
  { version: 1, created_at: "2026-08-20T12:00:00+00:00", reason: "as PixlStash first read it" },
];

/**
 * Stub the routes this screen reads and answer from *world*, which keeps what
 * a write stored and which a test may change as it goes. Returns what was
 * sent, and every workflow request nothing here answers.
 */
export async function stubWorkflows(page, world = {}) {
  const net = {
    world: {
      names: { [WF]: "Portrait, soft window light", [OTHER]: "Landscape, golden hour" },
      /** Per workflow id: the set waiting, or absent. */
      waiting: {},
      /** Per workflow id: its versions, newest first. */
      versions: { [WF]: VERSIONS.map((row) => ({ ...row })), [OTHER]: [] },
      verbs: { save: OPEN, save_as_new: OPEN },
      chain: CHAIN,
      restoreRefusal: "",
      /** Per workflow id: what waits was made on a version since left. */
      stale: {},
      /** ComfyUI configured, with the node that opens a workflow. */
      comfyui: false,
      ...world,
    },
    /** A held answer, by route: a gate to release (see `gate`). */
    hold: {},
    /** The writes in the order they reached the server. */
    order: [],
    changePuts: [],
    discards: [],
    saves: [],
    savesAsNew: [],
    restores: [],
    duplicates: [],
    openings: [],
    chainWrites: [],
    planAsks: [],
    unstubbed: [],
  };
  const json = (route, body, status = 200) => route.fulfill({ status, json: body });
  const versionOf = (id) => net.world.versions[id]?.[0]?.version ?? null;
  const cardOf = (id) => {
    const rows = net.world.versions[id] ?? [];
    return workflowCard(id, net.world.names[id], {
      versions: Math.max(rows.length, 1),
      version: rows[0]?.version ?? 1,
      version_at: rows[0]?.created_at ?? null,
      unsaved_changes: countOf(net.world.waiting[id]),
    });
  };
  const changesOf = (id) => {
    const waiting = net.world.waiting[id] ?? null;
    const stale = Boolean(net.world.stale[id]) && countOf(waiting) > 0;
    return {
      workflow_id: id,
      version: versionOf(id),
      waiting: countOf(waiting) ? waiting : null,
      count: countOf(waiting),
      ran: null,
      ...net.world.verbs,
      // As the real server answers for a set made on a version since left:
      // marked, and Save closed for it. Absent otherwise, as for a mock that
      // never heard of the field.
      ...(stale
        ? {
            stale: true,
            save: { open: false, reason: "This workflow has changed since." },
          }
        : {}),
      ran_save: null,
    };
  };
  const versionsOf = (id) => ({
    workflow_id: id,
    version: versionOf(id),
    versions: (net.world.versions[id] ?? []).map((row, index) => ({
      ...row,
      current: index === 0,
    })),
  });
  const addVersion = (id, reason) => {
    const rows = net.world.versions[id] ?? [];
    net.world.versions[id] = [
      { version: (rows[0]?.version ?? 0) + 1, created_at: NOW, reason },
      ...rows,
    ];
  };

  await page.route(
    (url) => url.pathname.startsWith("/api/v1/"),
    async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = decodeURIComponent(url.pathname).slice("/api/v1".length);
      const method = request.method();
      const { world: now } = net;

      if (path === "/models/workflow-sets" && method === "GET") return json(route, SHELF);
      // The dialog's Add reads the shelf's LoRAs; none is needed here.
      if (path === "/adapters" && method === "GET") return json(route, { adapters: [] });
      if (path === "/recipes" && method === "GET") return json(route, []);
      if (now.comfyui && path === "/users/me/config" && method === "GET") {
        const real = await route.fetch();
        return route.fulfill({
          response: real,
          json: { ...(await real.json()), comfyui_url: "http://127.0.0.1:8188" },
        });
      }
      if (now.comfyui && path === "/comfyui/pixlstash-node" && method === "GET") {
        return json(route, { can_open_workflows: true, saves_back: true });
      }
      if (!path.startsWith("/workflows")) return route.fallback();

      if (path === "/workflows" && method === "GET") {
        return json(route, {
          cards: Object.keys(now.names).map(cardOf),
          one_offs: 0,
          hidden: 0,
        });
      }
      if (path === "/workflows/run/preflight" && method === "POST") {
        const id = request.postDataJSON().workflow_id;
        return json(route, {
          ok: true,
          runs: 1,
          groups: [{ workflow_id: id, reasons: [], picture_inputs: [] }],
        });
      }

      const own = Object.keys(now.names).find(
        (id) => path === `/workflows/${id}` || path.startsWith(`/workflows/${id}/`),
      );
      const rest = own ? path.slice(`/workflows/${own}`.length) : null;
      if (rest === "" && method === "GET") {
        return json(route, {
          card: cardOf(own),
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
          source: { node_id: "4", class_type: "CheckpointLoaderSimple", outputs: ["MODEL", "CLIP"] },
          sink: { summary: "KSampler #12 reads model", consumers: [] },
          loaders: now.chain,
          added_loader_class: "LoraLoader",
          lanes: [],
          branch_note: null,
        });
      }
      if (rest === "/lora-chain" && method === "PUT") {
        const body = request.postDataJSON();
        const sent = (body.entries ?? []).map((entry) => String(entry.node_id));
        const changes = now.chain
          .filter((entry) => !sent.includes(entry.node_id))
          .map((entry) => ({
            kind: "deleted",
            node_id: entry.node_id,
            text: `${entry.name} is taken out of the chain.`,
          }));
        const kept = now.chain
          .map((entry) => entry.node_id)
          .filter((id) => sent.includes(id));
        if (JSON.stringify(kept) !== JSON.stringify(sent)) {
          changes.push({
            kind: "moved",
            node_id: sent[0],
            text: "The loaders are applied in another order.",
          });
        }
        if (!body.dry_run) net.chainWrites.push(body);
        return json(route, {
          dry_run: Boolean(body.dry_run),
          name: null,
          workflow_id: body.dry_run ? null : own,
          overwritten: !body.dry_run,
          changes,
        });
      }
      if (rest === "/lora-summary" && method === "GET") {
        return json(route, {
          workflow_id: own,
          pictures: 184,
          shared: [],
          varying: [],
          without: null,
          cover_asset: null,
        });
      }
      if (rest === "/form-inputs" && method === "GET") return json(route, { nodes: [] });
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
      if (rest === "/set-clone-plans" && method === "POST") {
        const body = request.postDataJSON();
        net.planAsks.push(body);
        return json(route, {
          base_filename: CKPT_FILE,
          base_model: "SDXL",
          plans: body.sets.map((ask, index) =>
            index === 0
              ? {
                  key: ask.key,
                  fit: "same_base_model",
                  reason: null,
                  keeps_loras: true,
                  swaps: {
                    [CKPT_FILE]: "SDXL/juggernautXL_v9.safetensors",
                    [VAE_FILE]: "vae/sdxl-vae-fp16.safetensors",
                  },
                  unpaired_bases: [],
                  takes: {},
                  choices: {},
                  loaders: [],
                }
              : {
                  key: ask.key,
                  fit: "wont_load",
                  reason: "Its checkpoint needs a UNETLoader, which this workflow does not have.",
                  keeps_loras: false,
                  swaps: {},
                  unpaired_bases: [],
                  takes: {},
                  choices: {},
                  loaders: [],
                },
          ),
        });
      }
      if (rest === "/changes" && method === "GET") return json(route, changesOf(own));
      if (rest === "/changes" && method === "PUT") {
        const body = request.postDataJSON();
        net.changePuts.push(body);
        net.order.push("put");
        if (net.hold.put) await net.hold.put.promise;
        now.waiting[own] = countOf(body) ? body : null;
        return json(route, changesOf(own));
      }
      if (rest === "/changes" && method === "DELETE") {
        net.discards.push(own);
        now.waiting[own] = null;
        return json(route, changesOf(own));
      }
      if (rest === "/versions" && method === "GET") return json(route, versionsOf(own));
      const restore = rest?.match(/^\/versions\/(\d+)\/restore$/);
      if (restore && method === "POST") {
        net.restores.push(Number(restore[1]));
        if (now.restoreRefusal) return json(route, { detail: now.restoreRefusal }, 409);
        addVersion(own, `went back to version ${restore[1]}`);
        // What waits was made on the version left: the server marks it so.
        if (countOf(now.waiting[own])) now.stale[own] = true;
        return json(route, versionsOf(own));
      }
      const opening = rest?.match(/^\/versions\/(\d+)\/opening$/);
      if (opening && method === "POST") {
        net.openings.push(Number(opening[1]));
        return json(route, { opening: "ab".repeat(16), workflow_id: own });
      }
      if (rest === "/duplicate" && method === "POST") {
        const version = url.searchParams.get("version");
        net.duplicates.push(version === null ? null : Number(version));
        // As the server names it: a plain duplicate is a copy.
        const name = `${now.names[own]} ${version === null ? "(copy)" : `(version ${version})`}`;
        now.names[NEW_WF] = name;
        now.versions[NEW_WF] = [];
        return json(route, { name, workflow_id: NEW_WF }, 201);
      }
      if (rest === "/save" && method === "POST") {
        const body = request.postDataJSON();
        net.saves.push(body);
        net.order.push("save");
        if (net.hold.save) await net.hold.save.promise;
        now.waiting[own] = null;
        now.stale[own] = false;
        addVersion(own, "models changed");
        return json(route, {
          workflow_id: own,
          version: versionOf(own),
          new_workflow: false,
          left_out: [],
        });
      }
      if (rest === "/save-as-new" && method === "POST") {
        const body = request.postDataJSON();
        net.savesAsNew.push(body);
        now.waiting[own] = null;
        now.names[NEW_WF] = body.name;
        now.versions[NEW_WF] = [];
        return json(
          route,
          { workflow_id: NEW_WF, name: body.name, new_workflow: true, version: 1, left_out: [] },
          201,
        );
      }

      net.unstubbed.push(`${method} ${path}${url.search}`);
      return json(route, { detail: "not stubbed by workflowTabStubs.js" }, 599);
    },
  );
  return net;
}

/** A promise a test releases by hand: what a route holds its answer on. */
export function gate() {
  let release;
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  return { promise, release };
}
