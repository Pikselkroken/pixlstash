import axios from 'axios';
import {computed, ref} from 'vue';

// Centralised authentication state
const isAuthenticated = ref(false);

// Share token state (set when app is loaded with ?token= query param)
let _shareToken = null;
const sessionContext = ref(null);
const isReadOnly = computed(() => sessionContext.value?.scope === 'READ');

// Per-tab client id, mirrored from useWsStore into module scope so the request
// interceptor can attach it without depending on Pinia being initialised. Used
// ONLY for echo-matching of our own WebSocket events - never for authorization.
// Capped at 200 chars to match the backend's X-Client-Id limit.
let _clientId = null;

function setRequestClientId(clientId) {
  _clientId =
    typeof clientId === 'string' && clientId ? clientId.slice(0, 200) : null;
}

// ── Auth-context transitions ────────────────────────────────────────────────
// Every credential change - login, logout, share-token entry, vault switch -
// is announced here, once, so no store has to invent its own detection. Any
// store holding scope-filtered server data (whose CONTENT is an authorization
// decision) registers here and drops that data synchronously, before the next
// render can show one credential's data to another.
const _sessionResetHandlers = new Set();

/**
 * Register a handler to run on every auth-context transition.
 * @param {Function} handler - called synchronously, with no arguments.
 * @returns {Function} unregisters the handler.
 */
function onSessionReset(handler) {
  if (typeof handler !== 'function') return () => {};
  _sessionResetHandlers.add(handler);
  return () => _sessionResetHandlers.delete(handler);
}

/**
 * Announce that the credential changed. Handlers run synchronously; one that
 * throws is logged and never stops the others.
 *
 * The transport's own identity - the share token it attaches to every request,
 * and the session context that `isReadOnly` is derived from - is dropped here
 * too, AFTER the handlers. Both outlived a credential change before (issue
 * #655 item 4): `Root.vue` calls `activateShareToken()` before validating the
 * token, so an invalid `?token=` left `_shareToken` set while the login screen
 * rendered, and the owner's subsequent login then attached a stale `token=`
 * query param to every request. The stale `resource_type` that came with it
 * suppressed the owner's own project list.
 *
 * Order is load-bearing, in both directions:
 *
 *   * AFTER the handlers, because a handler may read `sessionContext` while it
 *     decides what to drop (`useEntityListsStore.canFetch`). Clearing first
 *     would flip `isReadOnly` to false underneath them mid-reset.
 *   * BEFORE `activateShareToken` assigns the new token, which is why that
 *     function announces the transition first and assigns second.
 *
 * @param {string} reason - what changed, for the log line.
 */
function notifySessionReset(reason) {
  for (const handler of _sessionResetHandlers) {
    try {
      handler();
    } catch (error) {
      console.error(
          `Session-reset handler failed after ${reason}:`, error);
    }
  }
  _shareToken = null;
  sessionContext.value = null;
}

function activateShareToken(token) {
  // Entering a share token is a credential change: whatever the previous
  // context cached was scoped to a different principal.
  notifySessionReset('share-token entry');
  _shareToken = token;
}

/**
 * Mint a correlation id for one user gesture that fans out over several
 * requests (deleting a tag chip is a `remove_all` *and* a `reject`).
 *
 * Sent as `X-Operation-Batch-Id` on every request of the gesture, it becomes
 * the recorded operations' `batch_id`, so the whole gesture is one history step
 * and one Ctrl+Z. See docs/backend_architecture.md §21.2.
 *
 * The `cli-` prefix is load-bearing: the backend only accepts client ids in
 * that namespace, and mints its own as `srv-`, so the two can never collide.
 *
 * @returns {string} a fresh gesture batch id.
 */
function newOperationBatchId() {
  const random =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID().replace(/-/g, '')
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
  return `cli-${random}`;
}

/**
 * Axios config carrying a gesture batch id, or `undefined` when there is none.
 *
 * Every api module that participates in a compound gesture merges this into its
 * request config, which keeps the header spelling in exactly one place.
 *
 * @param {string} [batchId] - an id from {@link newOperationBatchId}.
 * @returns {Object|undefined} `{ headers: { 'X-Operation-Batch-Id': … } }`.
 */
function operationBatchHeaders(batchId) {
  if (!batchId) return undefined;
  return {headers: {'X-Operation-Batch-Id': batchId}};
}

/**
 * Append the active share token as a ?token= query parameter to a URL.
 * Should be used for all direct <img src> or similar browser-native requests
 * that bypass the axios interceptor.
 */
function appendShareToken(url) {
  if (!_shareToken || !url || !backendCredentialTarget(url, {
    allowWebSocket: true,
  })) return url;
  // Avoid double-appending if the token is already in the URL.
  if (url.includes(`token=${encodeURIComponent(_shareToken)}`)) return url;
  const [withoutHash, ...hashParts] = url.split('#');
  const sep = withoutHash.includes('?') ? '&' : '?';
  const hash = hashParts.length ? `#${hashParts.join('#')}` : '';
  return `${withoutHash}${sep}token=${encodeURIComponent(_shareToken)}${hash}`;
}

const DEFAULT_BACKEND_PORT = 9537;
const environmentBaseUrl = import.meta.env.VITE_BACKEND_URL;
const isDev = import.meta.env.DEV;
const API_PREFIX = '/api/v1';

function deriveBackendUrl() {
  if (environmentBaseUrl) return environmentBaseUrl;
  if (typeof window === 'undefined') {
    return `http://localhost:${DEFAULT_BACKEND_PORT}`;
  }
  const {protocol, hostname, port} = window.location;
  // In development the SPA is commonly served by Vite on 5173 while the API
  // server runs separately on 9537. Default to the backend port unless an
  // explicit VITE_BACKEND_URL is provided.
  if (isDev) {
    return `${protocol}//${hostname}:${DEFAULT_BACKEND_PORT}`;
  }
  const isStandardPort =
      (protocol === 'https:' && (port === '' || port === '443')) ||
      (protocol === 'http:' && (port === '' || port === '80'));
  return isStandardPort ? `${protocol}//${hostname}` : `${protocol}//${hostname}:${port}`;
}

const resolvedBaseUrl = deriveBackendUrl();
const apiBaseUrl = `${resolvedBaseUrl}${API_PREFIX}`;

/**
 * Parse a request target against the configured backend and decide whether it
 * is allowed to carry PixlStash credentials. URL parsing, not string matching,
 * is load-bearing here: suffix hosts, userinfo and alternate ports must never
 * inherit the backend's share token or per-tab client id.
 *
 * Relative references resolve against the configured backend origin. Absolute
 * and protocol-relative references must have exactly that origin. WebSocket
 * schemes are accepted only for browser-native socket URLs, where ws maps to
 * http and wss maps to https for the origin comparison.
 */
function backendCredentialTarget(rawUrl, {allowWebSocket = false} = {}) {
  if (typeof rawUrl !== 'string' || !rawUrl.trim()) return null;

  let backend;
  let target;
  try {
    const documentOrigin = typeof window !== 'undefined'
      ? window.location.origin
      : undefined;
    backend = new URL(resolvedBaseUrl, documentOrigin);
    target = new URL(rawUrl, `${backend.origin}/`);
  } catch {
    return null;
  }

  if (!['http:', 'https:'].includes(backend.protocol) ||
      backend.username || backend.password || target.username ||
      target.password) {
    return null;
  }

  if (allowWebSocket && ['ws:', 'wss:'].includes(target.protocol)) {
    target.protocol = target.protocol === 'wss:' ? 'https:' : 'http:';
  }
  if (!['http:', 'https:'].includes(target.protocol)) return null;
  return target.origin === backend.origin ? target : null;
}

/** Convert a trusted backend HTTP URL into its corresponding socket URL. */
function toBackendWebSocketUrl(rawUrl) {
  const target = backendCredentialTarget(rawUrl);
  if (!target) return '';
  target.protocol = target.protocol === 'https:' ? 'wss:' : 'ws:';
  return target.toString();
}

// Axios instance
const apiClient = axios.create({
  baseURL: resolvedBaseUrl,
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,  // Ensure cookies are included in requests
});

// Mutating verbs carry the per-tab X-Client-Id so the backend can echo it back
// on the resulting WebSocket event and the originating tab can suppress the
// reload for its own optimistic op.
const MUTATING_METHODS = new Set(['post', 'put', 'patch', 'delete']);

function isMutatingRequest(config) {
  const method = (config?.method || 'get').toLowerCase();
  return MUTATING_METHODS.has(method);
}

// A multipart body must not inherit this instance's JSON default. Axios 1.x
// reads the request's own Content-Type in `transformRequest`, and when it says
// `application/json` it converts a FormData into `JSON.stringify(
// formDataToJSON(form))` - a File or Blob serialises to `{}`, so
// `POST /models/{id}/icon` received the literal body `{"file":{}}` and answered
// 422. Cleared HERE rather than remembered at each call site: three uploaders
// passed `Content-Type: multipart/form-data` themselves and the fourth
// (`setModelIcon`) did not, which is the whole model-thumbnail verb, both its
// routes, silently dead.
//
// **Every** content type goes, not only the JSON one, which is why the name
// does not say `Json`. A hand-written `multipart/form-data` is wrong too: it
// carries no boundary, and only the thing that serialises the body can know
// one. Deleting is what hands that decision to the adapter, which then writes
// the header with the boundary it generated. So a caller cannot set a
// FormData's content type here - deliberately, because a caller cannot know it.
function stripContentTypeForFormData(config) {
  if (typeof FormData === 'undefined' || !(config?.data instanceof FormData)) {
    return;
  }
  const headers = config.headers;
  if (!headers) return;
  // `AxiosHeaders.delete` is the public way, and by this point in the pipeline
  // `config.headers` is one of those. Its own-property store is an
  // implementation detail, so the plain-object walk is the fallback rather than
  // the route - it also covers a config assembled by hand, e.g. in a test.
  if (typeof headers.delete === 'function') {
    headers.delete('Content-Type');
    return;
  }
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === 'content-type') delete headers[key];
  }
}

apiClient.interceptors.request.use((config) => {
  stripContentTypeForFormData(config);

  const rawUrl = config?.url;
  if (!rawUrl || typeof rawUrl !== 'string') {
    return config;
  }

  const target = backendCredentialTarget(rawUrl);
  if (!target) {
    // Axios inherits `withCredentials: true` from this instance. Disable it on
    // every untrusted or malformed absolute target as a second fail-closed
    // boundary; the browser must not attach destination cookies either.
    config.withCredentials = false;
    return config;
  }

  config.withCredentials = true;
  if (_shareToken) {
    config.params = {...(config.params || {}), token: _shareToken};
  }

  // Attach the per-tab client id to relative mutating requests.
  if (_clientId && isMutatingRequest(config)) {
    config.headers = {...(config.headers || {}), 'X-Client-Id': _clientId};
  }

  // Fully-qualified and protocol-relative backend URLs already carry their
  // complete path. Only relative API references need the API prefix.
  const trimmedUrl = rawUrl.trim();
  const isAbsoluteReference =
      /^[a-z][a-z\d+.-]*:/i.test(trimmedUrl) ||
      /^[\\/]{2}/.test(trimmedUrl);
  if (isAbsoluteReference) return config;

  if (rawUrl.startsWith(API_PREFIX)) {
    return config;
  }

  config.url = rawUrl.startsWith('/')
      ? `${API_PREFIX}${rawUrl}`
      : `${API_PREFIX}/${rawUrl}`;

  return config;
});

// Login function
async function login(username, password) {
  try {
    const response = await apiClient.post('/login', {username, password});
    notifySessionReset('login');
    isAuthenticated.value = true;  // Update authentication state
    if (typeof window !== 'undefined' && 'credentials' in navigator &&
        'PasswordCredential' in window && username && password) {
      try {
        const credential = new PasswordCredential({
          id: username,
          name: username,
          password,
        });
        await navigator.credentials.store(credential);
      } catch {
        // Storing credentials is best-effort; ignore failures.
      }
    }
    return response.data;  // Return response data for further use if needed
  } catch (error) {
    console.error('Login failed:', error);
    throw error;  // Re-throw the error for the caller to handle
  }
}

// Logout function
async function logout() {
  // Drop the previous session's cached, scope-filtered data FIRST: the POST can
  // fail or hang, and nothing that outlives this call may still be readable.
  notifySessionReset('logout');
  try {
    await apiClient.post('/logout');
  } catch (error) {
    console.error('Logout failed:', error);
  }
  isAuthenticated.value = false;  // Update authentication state}
}

// Check session function
async function checkSession() {
  try {
    const response = await apiClient.get('/check-session');
    isAuthenticated.value = true;  // Update authentication state
    return {status: 'ok', data: response.data};
  } catch (error) {
    if (error.response && error.response.status === 401) {
      console.warn('Session invalid or expired:', error);
      isAuthenticated.value = false;  // Update authentication state
      return {status: 'invalid'};
    }
    console.warn('Backend unreachable while checking session:', error);
    return {status: 'unreachable'};
  }
}

// Check if registration is required
async function checkLoginStatus() {
  try {
    const response = await apiClient.get('/login');
    return response.data;
  } catch (error) {
    console.error('Login status check failed:', error);
    throw error;
  }
}

// Interceptor to handle 401 errors globally
apiClient.interceptors.response.use((response) => response, (error) => {
  if (error.response && error.response.status === 401) {
    const url = error?.config?.url || '';
    // Don't log out when operating under a share token - a 401 just means
    // this particular endpoint isn't accessible to the token's scope.
    if (!url.includes('/users/me/auth') && !_shareToken) {
      console.error('Unauthorised! Logging out...');
      logout();  // Call the centralised logout function
    }
  }
  return Promise.reject(error);
});

export {
  apiClient,
  activateShareToken,
  appendShareToken,
  checkLoginStatus,
  checkSession,
  isAuthenticated,
  isReadOnly,
  login,
  logout,
  newOperationBatchId,
  notifySessionReset,
  onSessionReset,
  operationBatchHeaders,
  sessionContext,
  setRequestClientId,
  toBackendWebSocketUrl,
  apiBaseUrl as API_BASE_URL,
};
