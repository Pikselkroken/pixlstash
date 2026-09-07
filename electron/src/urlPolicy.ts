import { resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * The lookup key for a path, matching the platform's own idea of path identity:
 * resolved, and case-folded on Windows only.
 *
 * THE canonical rule, re-exported by `config.ts` (which cannot own it - it
 * imports `electron` at module load, and this module is loaded by tests that
 * have no Electron). Two copies of this rule is exactly the bug #1206 item 2
 * describes: `config.ts` folded case and this file did not, so a `file://` URL
 * whose drive letter came back in a different case than `RENDERER_DIR` spells
 * it made {@link isBundledRendererPage} answer false for the wizard page
 * itself - and `requireSetupRenderer` then refused every `setup:*` channel,
 * with no way through first-run setup.
 *
 * Windows filesystems are case-insensitive, so `C:\\x` and `c:\\x` are one
 * directory. POSIX is case-SENSITIVE, where folding would let `/opt/Renderer`
 * pass as `/opt/renderer` - a different directory, and one we did not bundle.
 */
export function pathKey(path: string, platform: NodeJS.Platform = process.platform): string {
  const resolved = resolve(path.trim());
  return platform === 'win32' ? resolved.toLowerCase() : resolved;
}

/**
 * A URL safe to write to the log: enough to identify what was blocked, never
 * the `user:password@` userinfo the URL may carry and never a page-authored
 * payload. Schemes with no origin report origin `null`; of those only `file:`
 * has a path worth logging (it names a real file), while a `data:` or
 * `javascript:` "path" IS the attacker-controlled body, so it is dropped and
 * only the scheme (plus the `data:` media type) is kept.
 */
export function redactUrl(target: string): string {
  let url: URL;
  try {
    url = new URL(target);
  } catch {
    return '<unparseable URL>';
  }
  let rendered: string;
  if (url.origin !== 'null') {
    rendered = url.origin + url.pathname;
  } else if (url.protocol === 'file:') {
    rendered = `file://${url.pathname}`;
  } else if (url.protocol === 'data:') {
    rendered = `data:${url.pathname.split(',')[0]},<redacted>`;
  } else {
    rendered = `${url.protocol}<redacted>`;
  }
  return rendered.length > 200 ? `${rendered.slice(0, 200)}…` : rendered;
}

/**
 * True only when `target` is exactly the bundled renderer file `page`.
 *
 * SECURITY: the first-run wizard and the app share one window and one preload,
 * so `setup.html` and the library page reach the same `ipcMain` handlers and a
 * `webContents` binding cannot tell them apart - the only thing that differs is
 * the document currently loaded in the sender. This answers that question for
 * the `setup:*` channels, which are the wizard's alone: `setup:commit` rewrites
 * the server config (dropping `external_server_enabled` and `port`, setting
 * `require_ssl: false`), repoints the library and restarts the backend.
 *
 * Deliberately stricter than {@link isAllowedNavigation}, which allows the whole
 * renderer directory because every file in it is ours to load. Here one named
 * file is the answer, so `index.html` and `permissions.html` are refused too.
 * The path is resolved and compared whole: a prefix test would accept a sibling
 * directory sharing the prefix, and `fileURLToPath` is what stops a percent-
 * encoded traversal reaching the comparison as text. Compared through
 * {@link pathKey}, so the two halves may disagree about casing on Windows -
 * where they name one directory - and never on POSIX, where they do not.
 */
export function isBundledRendererPage(
  target: string,
  rendererDir: string,
  page: string,
  platform: NodeJS.Platform = process.platform,
): boolean {
  let url: URL;
  try {
    url = new URL(target);
  } catch {
    return false;
  }
  if (url.protocol !== 'file:') return false;
  try {
    const dir = rendererDir.endsWith(sep) ? rendererDir : rendererDir + sep;
    return pathKey(fileURLToPath(url), platform) === pathKey(resolve(dir, page), platform);
  } catch {
    return false;
  }
}

/**
 * True only when `target` is a page served by the running backend itself.
 *
 * SECURITY: the counterpart to {@link isBundledRendererPage}, for the channels
 * that belong to the *app* rather than to the wizard. `server:setSettings`
 * flips `external_server_enabled` on, sets `host` to `0.0.0.0`, can clear
 * `require_ssl`, and restarts the backend - turning a local photo application
 * into a network service. That is the app's Settings dialog's job and nothing
 * else's, so a bundled `file://` page (the wizard, the splash, the permission
 * repair screen) is refused here even though the navigation guard is happy to
 * load it.
 *
 * Stricter than {@link isAllowedNavigation} in the other direction: only the
 * exact origin of the page actually loaded counts, never a `file://` URL and
 * never the pre-backend loopback fallback. Before the backend is up there is no
 * app, so `currentUrl` being null refuses everything.
 *
 * **What this does not do:** it does not defend against hostile JavaScript
 * running *inside* the app's own origin, which would pass. Nothing at this
 * boundary can - the app is the legitimate caller. It removes the other
 * renderer surfaces, and the backend's own refusal to expose an external
 * listener without an owner password (`listeners.py`) is what stands behind it.
 */
export function isBackendOrigin(target: string, currentUrl: string | null): boolean {
  if (!currentUrl) return false;
  let url: URL;
  let current: URL;
  try {
    url = new URL(target);
    current = new URL(currentUrl);
  } catch {
    return false;
  }
  // Same three rules isAllowedNavigation applies, for the same reasons: an
  // embedded credential is never part of anything we loaded, and an opaque
  // scheme can carry a matching origin (`blob:http://127.0.0.1:1234/x`) while
  // being a document the page authored rather than one the backend served.
  if (url.username || url.password) return false;
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
  return url.origin === current.origin;
}

/**
 * Decide whether the window may load `target` - used by BOTH the top-level
 * navigation guard and `setWindowOpenHandler`, so the origin policy lives in one
 * place the way the scheme policy already does. The privileged
 * `pixlstashDesktop` preload bridge stays injected across same-window
 * navigation (and is inherited by a child window), so any off-origin page that
 * loaded here could call high-impact IPC (setServerSettings, commitSetup,
 * installAccelerator, …). We therefore allow ONLY the content we load ourselves
 * and block everything else (deny-by-default):
 *
 *  - `file://` - ONLY files inside our own packaged renderer directory
 *    (renderer/index.html splash, renderer/setup.html, their assets). A blanket
 *    `file:` allow would let a navigated page load any local HTML under the
 *    privileged preload, so we resolve the target path and require it to live
 *    under `rendererDir`.
 *  - the live loopback backend origin - http://127.0.0.1:<ephemeral port>. The
 *    port is chosen fresh per backend launch, so the allowed origin is derived
 *    from the URL we actually loaded (`currentUrl`), never hardcoded. Before the
 *    backend is up `currentUrl` is null; we then permit only the loopback host
 *    (127.0.0.1 / localhost over http) so an in-flight load isn't broken, while
 *    still excluding every non-loopback origin.
 *
 * Every host check compares the PARSED hostname, never a string prefix: a prefix
 * test lets `http://127.0.0.1.example.com/` and `http://localhost.example.com/`
 * through as "loopback" (#1020). The scheme is checked too, because an opaque
 * scheme can carry a matching origin - `new URL('blob:http://127.0.0.1:1234/x')`
 * reports origin `http://127.0.0.1:1234` - and such a document is authored by
 * the page, not served by us.
 */
export function isAllowedNavigation(
  target: string,
  currentUrl: string | null,
  rendererDir: string,
  platform: NodeJS.Platform = process.platform,
): boolean {
  let url: URL;
  try {
    url = new URL(target);
  } catch (e) {
    console.warn(`[nav] blocking navigation to unparseable URL ${redactUrl(target)}:`, e);
    return false;
  }
  // Embedded credentials are never part of anything we load ourselves, and
  // `url.origin` deliberately ignores them - so refuse them rather than let
  // `http://someone@127.0.0.1:<port>/` reach the backend as the loopback origin.
  if (url.username || url.password) {
    console.warn(`[nav] blocking URL carrying embedded credentials: ${redactUrl(target)}`);
    return false;
  }
  // Local bundled pages (splash / setup wizard): allow ONLY our own renderer
  // files, never an arbitrary file:// path (which would still carry the preload).
  // Normalise the trailing separator here rather than trust the caller: without
  // it a sibling directory sharing the prefix (…/renderer-evil) would pass.
  if (url.protocol === 'file:') {
    // Through pathKey for the same reason isBundledRendererPage is: on Windows
    // a differently-cased drive letter names the same directory, and refusing
    // it would block our own bundled pages from loading.
    const base = pathKey(rendererDir, platform);
    try {
      const path = pathKey(fileURLToPath(url), platform);
      return path === base || path.startsWith(base + sep);
    } catch (e) {
      console.warn(`[nav] blocking unresolvable file:// URL ${redactUrl(target)}:`, e);
      return false;
    }
  }
  // Everything else must be a real http(s) document; blob:/data:/about: and any
  // custom scheme are refused before the origin comparison below sees them.
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
  // The running backend, pinned to the exact loopback origin we loaded. Once we
  // know that origin it is the ONLY http one allowed - another port on the same
  // loopback host is a different local service, and letting the window load it
  // would carry the privileged preload bridge onto its pages.
  if (currentUrl) {
    try {
      return url.origin === new URL(currentUrl).origin;
    } catch (e) {
      console.warn(`[nav] could not parse current backend URL ${redactUrl(currentUrl)}:`, e);
    }
  }
  // Fallback before the backend URL is known: only the loopback host over http.
  return url.protocol === 'http:' && (url.hostname === '127.0.0.1' || url.hostname === 'localhost');
}
