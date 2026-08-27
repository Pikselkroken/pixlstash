// Gesture batch ids: the client half of "one gesture, one undo step".
//
// A compound gesture (chip delete = remove_all + reject) stamps every request
// it issues with one `X-Operation-Batch-Id`; the backend records them as one
// batch and a single Ctrl+Z reverses the whole gesture
// (docs/backend_architecture.md §21.2).

import { beforeEach, describe, it, expect, vi } from "vitest";

const { axiosInstance, requestInterceptors } = vi.hoisted(() => {
  const requestInterceptors = [];
  return {
    requestInterceptors,
    axiosInstance: {
      get: vi.fn().mockResolvedValue({ data: {} }),
      post: vi.fn().mockResolvedValue({ data: {} }),
      interceptors: {
        request: { use: vi.fn((handler) => requestInterceptors.push(handler)) },
        response: { use: vi.fn() },
      },
    },
  };
});

// The module builds an axios instance at import time; stub it so the session
// tests below can call logout() without a real request.
vi.mock("axios", () => {
  return { default: { create: () => axiosInstance } };
});

import {
  API_BASE_URL,
  activateShareToken,
  appendShareToken,
  login,
  logout,
  newOperationBatchId,
  onSessionReset,
  operationBatchHeaders,
  sessionContext,
  setRequestClientId,
  toBackendWebSocketUrl,
} from "./apiClient";

const requestInterceptor = requestInterceptors[0];

// The real `AxiosHeaders`, reached past this file's own `vi.mock("axios")`.
// It is a named export of the package - `axios/unsafe/...` is a private path
// and would break on an axios release for no reason to do with this app.
const { AxiosHeaders } = await vi.importActual("axios");

beforeEach(() => {
  activateShareToken(null);
  setRequestClientId(null);
});

describe("newOperationBatchId", () => {
  // Load-bearing: the backend accepts client ids only in the `cli-` namespace
  // and mints its own as `srv-`, so a client can never name - and attach itself
  // to - a server-created batch. It also validates the charset and a bounded
  // length, and IGNORES anything else, which would silently unbatch the gesture.
  it("mints an id in the namespace and charset the backend accepts", () => {
    const id = newOperationBatchId();
    expect(id).toMatch(/^cli-[A-Za-z0-9_-]{4,76}$/);
    expect(id.length).toBeLessThanOrEqual(80);
  });

  it("is unique per gesture", () => {
    const ids = new Set(
      Array.from({ length: 50 }, () => newOperationBatchId()),
    );
    expect(ids.size).toBe(50);
  });
});

// The single chokepoint every store holding scope-filtered server data hangs
// its cache-drop on (issue #646, condition C1). One mechanism, not one per
// store - a store that had to detect a credential change itself would be a
// store that eventually misses one.
describe("onSessionReset", () => {
  it("fires on logout, before the request that ends the session", async () => {
    const calls = [];
    const stop = onSessionReset(() => calls.push("reset"));
    const pending = logout();
    // Synchronous: nothing that outlives this call may still hold the previous
    // credential's data, even if the POST hangs or fails.
    expect(calls).toEqual(["reset"]);
    await pending;
    stop();
  });

  it("fires on login, so the previous credential's data never carries over", async () => {
    const handler = vi.fn();
    const stop = onSessionReset(handler);
    await login("someone", "hunter2");
    expect(handler).toHaveBeenCalledTimes(1);
    stop();
  });

  it("fires on share-token entry", () => {
    const handler = vi.fn();
    const stop = onSessionReset(handler);
    activateShareToken("share-token-abc");
    expect(handler).toHaveBeenCalledTimes(1);
    stop();
  });

  it("stops calling a handler once it unregisters", () => {
    const handler = vi.fn();
    onSessionReset(handler)();
    activateShareToken("another-token");
    expect(handler).not.toHaveBeenCalled();
  });

  it("keeps going when one handler throws", () => {
    const survivor = vi.fn();
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const stopFirst = onSessionReset(() => {
      throw new Error("handler exploded");
    });
    const stopSecond = onSessionReset(survivor);
    activateShareToken("token");
    expect(survivor).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalled();
    stopFirst();
    stopSecond();
    error.mockRestore();
  });
});

// Issue #655 item 4. The transport's own identity outlived a credential change:
// `_shareToken` and `sessionContext` were set by `activateShareToken` and never
// cleared by anything.
describe("the transport's own identity is dropped on a session reset", () => {
  const relativeUrl = "/pictures/1/thumbnail";

  it("stops attaching a share token after a logout", async () => {
    activateShareToken("share-token-abc");
    expect(appendShareToken(relativeUrl)).toContain("token=share-token-abc");
    await logout();
    expect(appendShareToken(relativeUrl)).toBe(relativeUrl);
  });

  // The reachable path: Root.vue calls activateShareToken BEFORE validating the
  // token, so an invalid ?token= left it set while the login screen rendered.
  // The owner's login then attached that dead token to every request.
  it("stops attaching a rejected share token once the owner logs in", async () => {
    activateShareToken("invalid-token-from-a-bad-link");
    await login("owner", "hunter2");
    expect(appendShareToken(relativeUrl)).toBe(relativeUrl);
  });

  it("clears the session context, so a stale scope cannot suppress the next one's lists", async () => {
    activateShareToken("share-token-abc");
    sessionContext.value = { scope: "READ", resource_type: "character" };
    await logout();
    expect(sessionContext.value).toBeNull();
  });

  // The other direction: entering a share token must still leave that token
  // attached. `activateShareToken` announces the transition and assigns after,
  // and clearing in the wrong order would wipe the token it just set.
  it("keeps the NEW token when a share link is what caused the reset", () => {
    activateShareToken("first-token");
    activateShareToken("second-token");
    expect(appendShareToken(relativeUrl)).toContain("token=second-token");
  });

  // Handlers may read sessionContext while deciding what to drop
  // (useEntityListsStore.canFetch), so it must still be readable during the
  // handler loop and only be cleared afterwards.
  it("clears the context AFTER the handlers, not before", async () => {
    const seen = [];
    sessionContext.value = { scope: "READ", resource_type: "character" };
    const stop = onSessionReset(() => seen.push(sessionContext.value));
    await logout();
    expect(seen).toEqual([{ scope: "READ", resource_type: "character" }]);
    expect(sessionContext.value).toBeNull();
    stop();
  });
});

describe("operationBatchHeaders", () => {
  it("builds the header only when there is a gesture to correlate", () => {
    expect(operationBatchHeaders("cli-abcd1234")).toEqual({
      headers: { "X-Operation-Batch-Id": "cli-abcd1234" },
    });
    expect(operationBatchHeaders()).toBeUndefined();
    expect(operationBatchHeaders("")).toBeUndefined();
    expect(operationBatchHeaders(null)).toBeUndefined();
  });
});

describe("exact backend-origin credential policy", () => {
  const backend = new URL(API_BASE_URL);
  const backendOrigin = backend.origin;

  function intercept(url, method = "post") {
    return requestInterceptor({ url, method, headers: {} });
  }

  beforeEach(() => {
    activateShareToken("share-secret");
    setRequestClientId("client-tab");
  });

  it("attaches credentials to relative API URLs", () => {
    const config = intercept("/pictures/7");
    expect(config.url).toBe("/api/v1/pictures/7");
    expect(config.params).toEqual({ token: "share-secret" });
    expect(config.headers["X-Client-Id"]).toBe("client-tab");
    expect(config.withCredentials).toBe(true);
  });

  it("attaches credentials to the exact configured backend origin", () => {
    const url = `${backendOrigin}/api/v1/pictures/7`;
    const config = intercept(url);
    expect(config.url).toBe(url);
    expect(config.params).toEqual({ token: "share-secret" });
    expect(config.headers["X-Client-Id"]).toBe("client-tab");
    expect(config.withCredentials).toBe(true);
  });

  it("accepts a protocol-relative URL only for the configured backend", () => {
    const trusted = intercept(`//${backend.host}/api/v1/pictures/7`);
    const external = intercept("//cdn.example.test/image.webp");
    expect(trusted.params).toEqual({ token: "share-secret" });
    expect(trusted.headers["X-Client-Id"]).toBe("client-tab");
    expect(external.params).toBeUndefined();
    expect(external.headers["X-Client-Id"]).toBeUndefined();
    expect(external.withCredentials).toBe(false);
  });

  it.each([
    ["suffix host", `${backend.protocol}//${backend.hostname}.evil.test:${backend.port || "80"}/steal`],
    ["userinfo", `${backend.protocol}//${backend.host}@evil.test/steal`],
    ["alternate port", `${backend.protocol}//${backend.hostname}:${Number(backend.port || 80) + 1}/steal`],
    ["different scheme", `${backend.protocol === "https:" ? "http:" : "https:"}//${backend.host}/steal`],
    ["external absolute", "https://cdn.example.test/image.webp"],
    ["malformed absolute", "http://[::1/steal"],
  ])("fails closed for a %s URL", (_label, url) => {
    const config = intercept(url);
    expect(config.params).toBeUndefined();
    expect(config.headers["X-Client-Id"]).toBeUndefined();
    expect(config.withCredentials).toBe(false);
  });

  it("does not trust the SPA origin when it differs from the backend", () => {
    expect(window.location.origin).not.toBe(backendOrigin);
    const config = intercept(`${window.location.origin}/api/v1/pictures/7`);
    expect(config.params).toBeUndefined();
    expect(config.headers["X-Client-Id"]).toBeUndefined();
  });

  it("never attaches the mutating client id to a trusted GET", () => {
    const config = intercept(`${backendOrigin}/api/v1/pictures/7`, "get");
    expect(config.params).toEqual({ token: "share-secret" });
    expect(config.headers["X-Client-Id"]).toBeUndefined();
  });
});

describe("appendShareToken for ImageOverlay media URLs", () => {
  const backend = new URL(API_BASE_URL);

  beforeEach(() => activateShareToken("overlay-secret"));

  it("credentials relative and exact-origin absolute media URLs", () => {
    expect(appendShareToken("/pictures/1.webp")).toBe(
      "/pictures/1.webp?token=overlay-secret",
    );
    expect(
      appendShareToken(`${backend.origin}/pictures/1.webp#frame`),
    ).toBe(`${backend.origin}/pictures/1.webp?token=overlay-secret#frame`);
  });

  it.each([
    `${backend.protocol}//${backend.hostname}.evil.test:${backend.port || "80"}/image.webp`,
    `${backend.protocol}//${backend.host}@evil.test/image.webp`,
    `${backend.protocol}//${backend.hostname}:${Number(backend.port || 80) + 1}/image.webp`,
    `${backend.protocol === "https:" ? "http:" : "https:"}//${backend.host}/image.webp`,
    "//cdn.example.test/image.webp",
    "https://cdn.example.test/image.webp",
    "http://[::1/image.webp",
  ])("leaves an untrusted or malformed media URL unchanged", (url) => {
    expect(appendShareToken(url)).toBe(url);
  });
});

describe("backend WebSocket URL normalization", () => {
  const backend = new URL(API_BASE_URL);

  it("maps only a trusted backend HTTP URL to ws/wss", () => {
    const socket = new URL(
      toBackendWebSocketUrl(`${backend.origin}/api/v1/ws/updates`),
    );
    expect(socket.protocol).toBe(backend.protocol === "https:" ? "wss:" : "ws:");
    expect(socket.host).toBe(backend.host);
    expect(socket.pathname).toBe("/api/v1/ws/updates");
  });

  it.each([
    "https://external.example/ws",
    "//external.example/ws",
    "ws://external.example/ws",
    "http://[::1/ws",
  ])("refuses to normalize an untrusted socket source", (url) => {
    expect(toBackendWebSocketUrl(url)).toBe("");
  });

  it("allows appendShareToken only on the mapped backend socket origin", () => {
    activateShareToken("socket-secret");
    const trusted = toBackendWebSocketUrl(
      `${backend.origin}/api/v1/ws/updates`,
    );
    expect(appendShareToken(trusted)).toContain("token=socket-secret");
    expect(appendShareToken("wss://external.example/ws")).toBe(
      "wss://external.example/ws",
    );
  });
});

describe("a multipart body does not inherit the JSON default", () => {
  // The instance sets `Content-Type: application/json` for every request, and
  // axios 1.x reads THAT in `transformRequest`: a FormData under a JSON content
  // type is rewritten as `JSON.stringify(formDataToJSON(form))`, in which a File
  // or Blob serialises to `{}`. `POST /models/{id}/icon` therefore received the
  // body `{"file":{}}` and answered 422 - the model shelf's Set Thumbnail verb,
  // both of its routes, silently dead.
  //
  // Cleared in the interceptor rather than at each call site because three of
  // the four uploaders remembered to pass the header and the fourth did not.
  // The header is DELETED, never set: only the browser can write the boundary.
  //
  // The REAL `AxiosHeaders` is what production hands the interceptor, so the
  // first case below is the shape that actually ships rather than a stand-in
  // for it. A plain object is covered too, because a hand-assembled config is
  // what every other test in this file passes.
  function interceptForm(headers) {
    return requestInterceptor({
      url: "/models/12/icon",
      method: "post",
      data: new FormData(),
      headers,
    });
  }

  it("drops it from the AxiosHeaders production actually sends", () => {
    const headers = AxiosHeaders.from({
      Accept: "application/json, text/plain, */*",
      "Content-Type": "application/json",
    });
    const config = interceptForm(headers);
    expect(config.headers.get("Content-Type")).toBeUndefined();
    // Everything else survives: this clears one header, it does not reset them.
    expect(config.headers.get("Accept")).toContain("application/json");
  });

  it("drops it from a plain headers bag, however it is spelled", () => {
    expect(
      interceptForm({ "Content-Type": "application/json" }).headers[
        "Content-Type"
      ],
    ).toBeUndefined();
    expect(
      Object.keys(interceptForm({ "content-type": "application/json" }).headers),
    ).not.toContain("content-type");
  });

  it("leaves a JSON body's content type alone", () => {
    const config = requestInterceptor({
      url: "/models/icons/clear",
      method: "post",
      data: { ids: [12] },
      headers: AxiosHeaders.from({ "Content-Type": "application/json" }),
    });
    expect(config.headers.get("Content-Type")).toBe("application/json");
  });
});
