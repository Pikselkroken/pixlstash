// The model-shelf resource, and specifically the WIRE.
//
// Every consumer of this module mocks it out at the import boundary - the shelf
// store, the shelf view, the show panel and `AdapterTray` all replace
// `listAdapters` with a double - so until this file existed the real function
// body was executed by nothing in the repo. That is not a theoretical gap: the
// params here are snake_case because FastAPI reads them that way, and FastAPI
// silently DROPS a query param it does not declare. Rename `character_id` to
// `characterId` and every caller keeps passing its tests while the server
// answers with every adapter on the machine - which the tray would then render
// as one person's attachments, under a confident "N attached".
//
// So these assert the query string, not the arguments.

import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn(), put: vi.fn(), patch: vi.fn(), post: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  editModels,
  forgetModels,
  listAdapters,
  listCheckpoints,
  setAdapterAttachments,
} from "./modelShelf";

/** The `params` object of the single GET this call made. */
const sentParams = () => apiClient.get.mock.calls[0][1].params;

beforeEach(() => {
  apiClient.get.mockReset().mockResolvedValue({ data: { adapters: [] } });
  apiClient.put.mockReset().mockResolvedValue({ data: {} });
});

describe("api/modelShelf listAdapters", () => {
  it("sends the entity filters under the names the route declares", async () => {
    await listAdapters({ characterId: 7 });
    expect(apiClient.get).toHaveBeenCalledWith("/adapters", {
      params: { character_id: 7 },
    });

    apiClient.get.mockClear();
    await listAdapters({ setId: 12 });
    expect(sentParams()).toEqual({ set_id: 12 });
  });

  it("sends the other filters under theirs", async () => {
    await listAdapters({
      fileKind: "unknown",
      baseModel: "flux.1-dev",
      kind: "lora",
      q: "ivy",
    });
    expect(sentParams()).toEqual({
      file_kind: "unknown",
      base_model: "flux.1-dev",
      kind: "lora",
      q: "ivy",
    });
  });

  it("omits every filter it was not given", async () => {
    // An empty `params` is what makes this the unfiltered list. A key present
    // with `undefined` would be dropped by axios today and is one refactor away
    // from being serialised as the string "undefined".
    await listAdapters();
    expect(sentParams()).toEqual({});
  });

  it("keeps a filter whose id is 0 rather than truth-testing it away", async () => {
    // Both ids, because the null-compare is written twice and a truthy test in
    // either one is a filter that silently disappears.
    await listAdapters({ characterId: 0 });
    expect(sentParams()).toEqual({ character_id: 0 });

    apiClient.get.mockClear();
    await listAdapters({ setId: 0 });
    expect(sentParams()).toEqual({ set_id: 0 });
  });

  it("returns the adapters array, and an empty one when the body has none", async () => {
    apiClient.get.mockResolvedValue({ data: { adapters: [{ id: 1 }] } });
    expect(await listAdapters()).toEqual([{ id: 1 }]);

    // The callers index straight into the result, so a shape the server has not
    // sent must not reach them as `undefined`.
    apiClient.get.mockResolvedValue({ data: {} });
    expect(await listAdapters()).toEqual([]);
  });
});

describe("api/modelShelf setAdapterAttachments", () => {
  it("sends only the two keys the request model allows", async () => {
    // The response model allows extra keys and the request model forbids them,
    // so echoing a row's `attachments` back verbatim starts failing the day the
    // server adds a field to the response.
    await setAdapterAttachments("a".repeat(64), [
      { entity_type: "character", entity_id: 7, created_at: "2026-08-01" },
    ]);
    const [url, body] = apiClient.put.mock.calls[0];
    expect(url).toBe(`/adapters/${"a".repeat(64)}/attachments`);
    expect(body).toEqual([{ entity_type: "character", entity_id: 7 }]);
  });

  it("escapes the hash into the path", async () => {
    await setAdapterAttachments("a/b", []);
    expect(apiClient.put.mock.calls[0][0]).toBe("/adapters/a%2Fb/attachments");
  });
});

// The rest of the module's wire. The two routes that WRITE were the ones left
// executed by nothing: a camelCase slip in `editModels`' spread is the same bug
// class as the one above, on a route that changes stored curation, and
// `forgetModels` destroys it.
describe("api/modelShelf the rest of the wire", () => {
  beforeEach(() => {
    apiClient.patch.mockReset().mockResolvedValue({ data: {} });
    apiClient.post.mockReset().mockResolvedValue({ data: {} });
  });

  it("listCheckpoints sends its filters under the route's names", async () => {
    apiClient.get.mockResolvedValue({ data: { checkpoints: [] } });
    await listCheckpoints({ baseModel: "UNASSIGNED", q: "sdxl" });
    expect(apiClient.get).toHaveBeenCalledWith("/checkpoints", {
      params: { base_model: "UNASSIGNED", q: "sdxl" },
    });
  });

  it("editModels sends the ids beside the changed columns, and only those", async () => {
    // Only the keys actually passed are sent - that is what lets one route
    // carry Rename, Set base model and Set kind without each blanking the
    // others - and an explicit null is a CLEAR, not "leave it alone".
    await editModels([1, 2], { base_model: null });
    expect(apiClient.patch).toHaveBeenCalledWith("/models", {
      ids: [1, 2],
      base_model: null,
    });
  });

  it("forgetModels posts to the forget route", async () => {
    await forgetModels([3]);
    expect(apiClient.post).toHaveBeenCalledWith("/models/forget", { ids: [3] });
  });
});
