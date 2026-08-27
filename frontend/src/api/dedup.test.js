import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  getPolicy,
  listGroups,
  getCounts,
  startScan,
  stackGroup,
  keepGroupSeparate,
  applyVerdictBatch,
  reopenGroup,
  autoStackExact,
  scopeBody,
  policyBody,
  listStackMembers,
  MAX_STACK_MEMBER_PAGE,
  listMixedStacks,
  splitMixedStack,
  unstackMixedStack,
  keepMixedStack,
  clearMixedStackKeep,
  MAX_MIXED_STACK_PAGE,
  GLOBAL_SCOPE,
} from "./dedup";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.patch.mockReset();
  apiClient.delete.mockReset();
});

describe("api/dedup - scope and policy fragments", () => {
  // ScopeRequestModel forbids extra fields, so a label or a glyph here is a 422.
  it("scopeBody emits only the two fields the server accepts", () => {
    expect(scopeBody("set", 12)).toEqual({ scope_type: "set", scope_id: "12" });
  });

  it("scopeBody omits scope_id for the global scope", () => {
    expect(scopeBody()).toEqual({ scope_type: GLOBAL_SCOPE });
    expect(scopeBody(GLOBAL_SCOPE, 5)).toEqual({ scope_type: GLOBAL_SCOPE });
  });

  // A scope id may be a numeric collection id or an absolute folder path, so it
  // is always sent as a string.
  it("scopeBody stringifies the id", () => {
    expect(scopeBody("folder", "/mnt/photos").scope_id).toBe("/mnt/photos");
    expect(scopeBody("project", 7).scope_id).toBe("7");
  });

  // An omitted field means "use the server default", which is not the same as
  // the client re-stating the default it happens to know.
  it("policyBody omits everything the caller did not set", () => {
    expect(policyBody()).toEqual({});
    expect(policyBody({ nearEnabled: false })).toEqual({ near_enabled: false });
    expect(policyBody({ threshold: 0.9, embeddingEnabled: true })).toEqual({
      threshold: 0.9,
      embedding_enabled: true,
    });
  });
});

describe("api/dedup - the policy endpoint", () => {
  it("getPolicy reads the defaults and bounds", async () => {
    apiClient.get.mockResolvedValue({
      data: { defaults: { threshold: 0.9 }, bounds: { min_threshold: 0.65 } },
    });
    const result = await getPolicy();
    expect(apiClient.get).toHaveBeenCalledWith("/dedup/policy");
    expect(result.bounds.min_threshold).toBe(0.65);
  });
});

describe("api/dedup - the queue", () => {
  it("listGroups reads the first page with the tier gate off", async () => {
    apiClient.get.mockResolvedValue({ data: { groups: [], scan: {} } });
    await listGroups();
    expect(apiClient.get).toHaveBeenCalledWith(
      "/dedup/groups",
      expect.objectContaining({
        params: {
          near_enabled: false,
          embedding_enabled: false,
          offset: 0,
          limit: 20,
          scope_type: GLOBAL_SCOPE,
        },
      }),
    );
  });

  // The decided page's own filter. The open queue's groups carry no verdict, so
  // the server refuses the param there - the client must not send it either.
  it("listGroups sends the verdict filter on the decided page only", async () => {
    apiClient.get.mockResolvedValue({ data: { groups: [] } });
    await listGroups({ decided: true, verdicts: ["stacked"] });
    expect(apiClient.get.mock.calls[0][1].params.verdict).toEqual(["stacked"]);
    // A repeatable query param, not axios' default `verdict[]=` - that key
    // means nothing to FastAPI and the filter would be dropped in silence.
    expect(apiClient.get.mock.calls[0][1].paramsSerializer).toEqual({
      indexes: null,
    });

    await listGroups({ verdicts: ["stacked"] });
    expect(apiClient.get.mock.calls[1][1].params.verdict).toBeUndefined();

    await listGroups({ decided: true });
    expect(apiClient.get.mock.calls[2][1].params.verdict).toBeUndefined();
  });

  it("listGroups pages by offset", async () => {
    apiClient.get.mockResolvedValue({ data: { groups: [] } });
    await listGroups({ offset: 40, limit: 20 });
    expect(apiClient.get).toHaveBeenCalledWith(
      "/dedup/groups",
      expect.objectContaining({
        params: expect.objectContaining({ offset: 40, limit: 20 }),
      }),
    );
  });

  // The cursor is the primary path: a keyset position cannot re-serve or skip a
  // group while a scan inserts rows, which an offset over the same ordering can.
  it("listGroups pages by cursor when it holds one", async () => {
    apiClient.get.mockResolvedValue({ data: { groups: [] } });
    await listGroups({ cursor: "eyJjIjowfQ", offset: 40 });
    const params = apiClient.get.mock.calls[0][1].params;
    expect(params.cursor).toBe("eyJjIjowfQ");
    // Never both: a server free to choose between them could silently keep the
    // weaker one.
    expect(params.offset).toBeUndefined();
  });

  it("listGroups ignores an empty cursor and pages by offset", async () => {
    apiClient.get.mockResolvedValue({ data: { groups: [] } });
    await listGroups({ cursor: "", offset: 20 });
    const params = apiClient.get.mock.calls[0][1].params;
    expect(params.offset).toBe(20);
    expect(params.cursor).toBeUndefined();
  });

  it("listGroups sends the tier gate as two booleans", async () => {
    apiClient.get.mockResolvedValue({ data: { groups: [] } });
    await listGroups({ nearEnabled: true, embeddingEnabled: true });
    expect(apiClient.get).toHaveBeenCalledWith(
      "/dedup/groups",
      expect.objectContaining({
        params: expect.objectContaining({
          near_enabled: true,
          embedding_enabled: true,
        }),
      }),
    );
  });

  // The threshold's floor is server policy. Omitting it means "use the server
  // default"; re-stating a value the client guessed would duplicate the bound.
  it("listGroups omits an unset threshold", async () => {
    apiClient.get.mockResolvedValue({ data: { groups: [] } });
    await listGroups();
    expect(apiClient.get.mock.calls[0][1].params.threshold).toBeUndefined();
    await listGroups({ threshold: 0.75 });
    expect(apiClient.get.mock.calls[1][1].params.threshold).toBe(0.75);
  });

  it("listGroups sends a scoped queue as a type/id pair", async () => {
    apiClient.get.mockResolvedValue({ data: { groups: [] } });
    await listGroups({ scopeType: "set", scopeId: 12 });
    expect(apiClient.get).toHaveBeenCalledWith(
      "/dedup/groups",
      expect.objectContaining({
        params: expect.objectContaining({
          scope_type: "set",
          scope_id: "12",
        }),
      }),
    );
  });

  it("listGroups returns the body, not the axios envelope", async () => {
    apiClient.get.mockResolvedValue({
      data: { groups: [{ signature: "sig-1" }], total: 143 },
    });
    const result = await listGroups();
    expect(result.total).toBe(143);
    expect(result.groups[0].signature).toBe("sig-1");
  });
});

describe("api/dedup - counts", () => {
  // The global badge comes back whether or not a scope was asked for, which is
  // what stops the sidebar and a context menu quoting different numbers.
  it("getCounts posts an empty scope list for the badge alone", async () => {
    apiClient.post.mockResolvedValue({ data: { unresolved_groups: 143 } });
    const result = await getCounts();
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/counts", {
      scopes: [],
    });
    expect(result.unresolved_groups).toBe(143);
  });

  it("getCounts asks for several scopes in one request", async () => {
    apiClient.post.mockResolvedValue({ data: { scopes: [] } });
    await getCounts({
      scopes: [
        { scopeType: "set", scopeId: 12 },
        { scopeType: "project", scopeId: 7 },
      ],
    });
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/counts", {
      scopes: [
        { scope_type: "set", scope_id: "12" },
        { scope_type: "project", scope_id: "7" },
      ],
    });
  });

  it("getCounts forwards the tier policy so the counts match the queue", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await getCounts({ policy: { nearEnabled: true } });
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/counts", {
      scopes: [],
      policy: { near_enabled: true },
    });
  });
});

describe("api/dedup - scans", () => {
  it("startScan nests the scope in the body", async () => {
    apiClient.post.mockResolvedValue({ data: { status: "running" } });
    await startScan({ scopeType: "folder", scopeId: "/mnt/a" });
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/scan", {
      scope: { scope_type: "folder", scope_id: "/mnt/a" },
    });
  });

  it("startScan scans the whole vault by default", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await startScan();
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/scan", {
      scope: { scope_type: GLOBAL_SCOPE },
    });
  });
});

describe("api/dedup - verdicts", () => {
  // The signature rides in the body, so it never has to survive a path.
  it("stackGroup posts the signature, cover and exclusions", async () => {
    apiClient.post.mockResolvedValue({ data: { stack_id: 5 } });
    await stackGroup("sig-1", {
      coverPictureId: 42,
      excludedPictureIds: [43],
    });
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/verdicts/stack", {
      signature: "sig-1",
      cover_picture_id: 42,
      excluded_picture_ids: [43],
    });
  });

  // An omitted cover means "the server's preselection stands", which is not the
  // same as sending null.
  it("stackGroup omits an absent cover and an empty exclusion list", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await stackGroup("sig-1");
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/verdicts/stack", {
      signature: "sig-1",
    });
  });

  it("stackGroup carries a batch id so several verdicts reverse as one", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await stackGroup("sig-1", { batchId: "b-1" });
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/verdicts/stack", {
      signature: "sig-1",
      batch_id: "b-1",
    });
  });

  it("keepGroupSeparate posts to its own verdict route", async () => {
    apiClient.post.mockResolvedValue({ data: { verdict: "keep_separate" } });
    const result = await keepGroupSeparate("sig-2");
    expect(apiClient.post).toHaveBeenCalledWith(
      "/dedup/verdicts/keep-separate",
      { signature: "sig-2" },
    );
    expect(result.verdict).toBe("keep_separate");
  });

  it("posts a multi-group gesture as one atomic verdict batch", async () => {
    apiClient.post.mockResolvedValue({
      data: { batch_id: "cli-gesture-1", results: [] },
    });
    await applyVerdictBatch(
      [
        {
          verdict: "stacked",
          signature: "sig-1",
          coverPictureId: 42,
          excludedPictureIds: [43],
        },
        { verdict: "keep_separate", signature: "sig-2" },
      ],
      { batchId: "cli-gesture-1" },
    );
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/verdicts/batch", {
      actions: [
        {
          verdict: "stacked",
          signature: "sig-1",
          cover_picture_id: 42,
          excluded_picture_ids: [43],
        },
        { verdict: "keep_separate", signature: "sig-2" },
      ],
      batch_id: "cli-gesture-1",
    });
  });

  it("reopenGroup posts the signature alone by default", async () => {
    apiClient.post.mockResolvedValue({ data: { previous_verdict: "stacked" } });
    await reopenGroup("sig-3");
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/verdicts/reopen", {
      signature: "sig-3",
    });
  });

  it("reopenGroup carries a gesture batch id when given one", async () => {
    apiClient.post.mockResolvedValue({ data: { previous_verdict: "stacked" } });
    await reopenGroup("sig-3", { batchId: "cli-clear-1" });
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/verdicts/reopen", {
      signature: "sig-3",
      batch_id: "cli-clear-1",
    });
  });
});

describe("api/dedup - bulk auto-stack", () => {
  // The safe direction is the default: a caller that forgets the flag counts
  // rather than writes.
  it("autoStackExact defaults to a dry run", async () => {
    apiClient.post.mockResolvedValue({ data: { groups: 1204 } });
    const result = await autoStackExact();
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/auto-stack", {
      scope: { scope_type: GLOBAL_SCOPE },
      dry_run: true,
    });
    expect(result.groups).toBe(1204);
  });

  it("autoStackExact commits with dry_run false and returns the batch id", async () => {
    apiClient.post.mockResolvedValue({ data: { batch_id: "b-1" } });
    const result = await autoStackExact({ dryRun: false });
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/auto-stack", {
      scope: { scope_type: GLOBAL_SCOPE },
      dry_run: false,
    });
    expect(result.batch_id).toBe("b-1");
  });

  it("autoStackExact scopes and caps a paged run", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await autoStackExact({
      dryRun: false,
      scopeType: "character",
      scopeId: 2,
      limit: 500,
    });
    expect(apiClient.post).toHaveBeenCalledWith("/dedup/auto-stack", {
      scope: { scope_type: "character", scope_id: "2" },
      dry_run: false,
      limit: 500,
    });
  });
});

describe("api/dedup: the deck expansion's lazy members", () => {
  // The eager half (each stack's real member count and its leader) rides on the
  // queue row; this is the only route that returns the members themselves, and
  // it exists so a 40-member stack is not inlined behind a row with room for
  // none.
  it("reads one page of a stack's members by plain offset", async () => {
    apiClient.get.mockResolvedValue({
      data: { stack_id: 12, member_count: 4, next_offset: null, members: [] },
    });
    const result = await listStackMembers(12, { offset: 50 });
    expect(apiClient.get).toHaveBeenCalledWith("/dedup/stacks/12/members", {
      params: { offset: 50 },
    });
    expect(result.member_count).toBe(4);
  });

  // Omitting the limit leaves the server's own default in force rather than the
  // client restating it.
  it("sends no limit unless one was asked for", async () => {
    apiClient.get.mockResolvedValue({ data: {} });
    await listStackMembers(7);
    expect(apiClient.get).toHaveBeenCalledWith("/dedup/stacks/7/members", {
      params: { offset: 0 },
    });
  });

  // The server caps the page; asking past the cap is a 422, so the client
  // clamps rather than discovering it at runtime.
  it("clamps an over-large page to the server's ceiling", async () => {
    apiClient.get.mockResolvedValue({ data: {} });
    await listStackMembers(7, { limit: 5000 });
    expect(apiClient.get).toHaveBeenCalledWith("/dedup/stacks/7/members", {
      params: { offset: 0, limit: MAX_STACK_MEMBER_PAGE },
    });
  });
});

describe("api/dedup: mixed stacks", () => {
  // The whole verdict is threshold-relative: the same stack is mixed at 0.90
  // and one clean cluster at 0.65. A caller that forgot to send the queue's
  // own value would be reading a different library from the one on screen.
  it("listMixedStacks sends the threshold it was given", async () => {
    apiClient.get.mockResolvedValue({ data: { stacks: [], total: 0 } });
    await listMixedStacks({ threshold: 0.65, offset: 20, limit: 50 });
    expect(apiClient.get).toHaveBeenCalledWith("/dedup/mixed-stacks", {
      params: { offset: 20, threshold: 0.65, limit: 50 },
    });
  });

  // Omitted means "the server's default", never "whatever the client guessed".
  it("listMixedStacks omits a threshold and a limit it was not given", async () => {
    apiClient.get.mockResolvedValue({ data: { stacks: [] } });
    await listMixedStacks();
    expect(apiClient.get).toHaveBeenCalledWith("/dedup/mixed-stacks", {
      params: { offset: 0 },
    });
  });

  it("listMixedStacks clamps the page to the server's own ceiling", async () => {
    apiClient.get.mockResolvedValue({ data: { stacks: [] } });
    await listMixedStacks({ limit: 5000 });
    expect(apiClient.get.mock.calls[0][1].params.limit).toBe(
      MAX_MIXED_STACK_PAGE,
    );
  });

  it("listMixedStacks asks for the kept rows only when told to", async () => {
    apiClient.get.mockResolvedValue({ data: { stacks: [] } });
    await listMixedStacks({ includeKept: true });
    expect(apiClient.get.mock.calls[0][1].params.include_kept).toBe(true);
    apiClient.get.mockClear();
    await listMixedStacks({ includeKept: false });
    expect(apiClient.get.mock.calls[0][1].params.include_kept).toBeUndefined();
  });

  it("listMixedStacks returns the body, not the envelope", async () => {
    apiClient.get.mockResolvedValue({ data: { total: 26, stacks: [{}] } });
    expect(await listMixedStacks()).toEqual({ total: 26, stacks: [{}] });
  });

  // "Send the ids the row showed": the split has to match what the user was
  // looking at, even if the stack has changed since they looked.
  it("splitMixedStack sends the ids the row showed", async () => {
    apiClient.post.mockResolvedValue({ data: { stack_id: 42 } });
    await splitMixedStack(42, {
      pictureIds: [11],
      threshold: 0.9,
      batchId: "cli-split-3",
    });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/dedup/mixed-stacks/42/split",
      {
        picture_ids: [11],
        threshold: 0.9,
        batch_id: "cli-split-3",
      },
    );
  });

  it("splitMixedStack omits an empty id list so the server recomputes", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await splitMixedStack(42, { pictureIds: [] });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/dedup/mixed-stacks/42/split",
      {},
    );
  });

  it("unstackMixedStack posts to the stack's own unstack route", async () => {
    apiClient.post.mockResolvedValue({ data: { stack_dissolved: true } });
    const result = await unstackMixedStack(7, { batchId: "cli-unstack-9" });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/dedup/mixed-stacks/7/unstack",
      { batch_id: "cli-unstack-9" },
    );
    expect(result.stack_dissolved).toBe(true);
  });

  // Keep changes no picture, so there is no batch id to send and nothing for
  // undo to restore. DELETE on the same path is the whole way back.
  it("keepMixedStack posts an empty body and DELETE clears it", async () => {
    apiClient.post.mockResolvedValue({ data: { dismissed: true } });
    apiClient.delete.mockResolvedValue({ data: { dismissed: false } });
    expect((await keepMixedStack(42)).dismissed).toBe(true);
    expect(apiClient.post).toHaveBeenCalledWith(
      "/dedup/mixed-stacks/42/keep",
      {},
    );
    expect((await clearMixedStackKeep(42)).dismissed).toBe(false);
    expect(apiClient.delete).toHaveBeenCalledWith(
      "/dedup/mixed-stacks/42/keep",
    );
  });

  // Every route is spelled relative. The SPA and the backend are different
  // origins in the dev server, the demo and Electron; that origin is now the
  // apiClient instance's baseURL rather than a per-call argument, so what these
  // functions must get right is the path.
  it("spells every mixed-stack route relative to the api root", async () => {
    apiClient.get.mockResolvedValue({ data: {} });
    apiClient.post.mockResolvedValue({ data: {} });
    apiClient.delete.mockResolvedValue({ data: {} });
    await listMixedStacks();
    await splitMixedStack(1);
    await unstackMixedStack(1);
    await keepMixedStack(1);
    await clearMixedStackKeep(1);
    expect(apiClient.get.mock.calls[0][0]).toBe("/dedup/mixed-stacks");
    expect(apiClient.post.mock.calls.map((c) => c[0])).toEqual([
      "/dedup/mixed-stacks/1/split",
      "/dedup/mixed-stacks/1/unstack",
      "/dedup/mixed-stacks/1/keep",
    ]);
    expect(apiClient.delete.mock.calls[0][0]).toBe(
      "/dedup/mixed-stacks/1/keep",
    );
  });
});
