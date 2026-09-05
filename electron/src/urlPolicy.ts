import { resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

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
    const dir = rendererDir.endsWith(sep) ? rendererDir : rendererDir + sep;
    try {
      const path = resolve(fileURLToPath(url));
      return path === dir.slice(0, -1) || path.startsWith(dir);
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
