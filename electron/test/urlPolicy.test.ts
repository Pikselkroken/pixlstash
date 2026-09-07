import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';
import { pathToFileURL } from 'node:url';
import { join, resolve, sep } from 'node:path';
import {
  isAllowedNavigation,
  isBackendOrigin,
  isBundledRendererPage,
  redactUrl,
} from '../src/urlPolicy';

// A plausible packaged renderer dir, resolved and separator-suffixed exactly as
// main.ts builds RENDERER_DIR.
const RENDERER_DIR = resolve('/opt/pixlstash/dist/renderer') + sep;
const BACKEND = 'http://127.0.0.1:8723/';

/** The window-open handler and the navigation guard share this one predicate. */
const allowed = (target: string, currentUrl: string | null = BACKEND): boolean =>
  isAllowedNavigation(target, currentUrl, RENDERER_DIR);

describe('isAllowedNavigation — backend origin', () => {
  it('allows the exact running backend origin', () => {
    assert.ok(allowed('http://127.0.0.1:8723/'));
    assert.ok(allowed('http://127.0.0.1:8723/pictures?page=2'));
  });

  it('rejects hosts that merely start with the loopback spelling (#1020)', () => {
    for (const bad of [
      'http://127.0.0.1.example.com/',
      'http://localhost.example.com/',
      'http://127.0.0.1.example.com:8723/',
      'http://127.0.0.1evil.example.com/',
      'http://localhostx.example.com/',
    ]) {
      assert.equal(allowed(bad), false, `${bad} must not count as loopback`);
      assert.equal(allowed(bad, null), false, `${bad} must not count as loopback pre-boot`);
    }
  });

  it('rejects a different port on the loopback host once the backend URL is known', () => {
    assert.equal(allowed('http://127.0.0.1:9999/'), false);
    assert.equal(allowed('http://localhost:8723/'), false);
  });

  it('rejects a scheme other than the backend one, even at a matching origin', () => {
    assert.equal(allowed('https://127.0.0.1:8723/'), false);
    // `new URL('blob:http://127.0.0.1:8723/x').origin` IS the backend origin, so
    // the scheme check is what keeps a page-authored blob document out.
    assert.equal(allowed('blob:http://127.0.0.1:8723/9c1e-uuid'), false);
    assert.equal(allowed('data:text/html,<script>1</script>'), false);
    assert.equal(allowed('javascript:alert(1)'), false);
    assert.equal(allowed('about:blank'), false);
    assert.equal(allowed('ftp://127.0.0.1:8723/'), false);
  });

  it('rejects embedded credentials even when the origin matches', () => {
    assert.equal(allowed('http://someone:example-password@127.0.0.1:8723/'), false);
    assert.equal(allowed('http://someone@127.0.0.1:8723/'), false);
    assert.equal(allowed('http://someone@127.0.0.1:8723/', null), false);
  });

  it('rejects an unparseable URL', () => {
    assert.equal(allowed('not a url'), false);
    // Non-numeric port: parsing throws rather than yielding a hostname.
    assert.equal(allowed('http://127.0.0.1:8723.example.com/'), false);
  });

  it('falls back to the loopback host only, before the backend URL is known', () => {
    assert.ok(allowed('http://127.0.0.1:41234/', null));
    assert.ok(allowed('http://localhost:41234/', null));
    assert.ok(allowed('http://LOCALHOST:41234/', null), 'hostnames are case-insensitive');
    assert.equal(allowed('http://192.0.2.10:41234/', null), false);
    assert.equal(allowed('https://example.com/', null), false);
    // Not currently reachable (the backend binds 127.0.0.1) but pinned so a
    // future ::1 bind is a deliberate change rather than a surprise.
    assert.equal(allowed('http://[::1]:41234/', null), false);
    assert.equal(allowed('http://localhost./', null), false);
  });
});

describe('isAllowedNavigation — bundled renderer files', () => {
  it('allows files inside the packaged renderer directory', () => {
    assert.ok(allowed(pathToFileURL(RENDERER_DIR + 'index.html').href));
    assert.ok(allowed(pathToFileURL(RENDERER_DIR + 'assets/app.js').href));
    assert.ok(allowed(pathToFileURL(RENDERER_DIR.slice(0, -1)).href));
  });

  it('rejects file:// paths outside it, including a sibling sharing the prefix', () => {
    const sibling = pathToFileURL(resolve('/opt/pixlstash/dist/renderer-evil/x.html')).href;
    assert.equal(allowed(pathToFileURL(resolve('/etc/passwd')).href), false);
    assert.equal(allowed(sibling), false);
    assert.equal(allowed(pathToFileURL(RENDERER_DIR + '../../secret.html').href), false);
  });

  it('rejects the prefix-sharing sibling even if the caller omits the trailing separator', () => {
    const unsuffixed = RENDERER_DIR.slice(0, -1);
    const sibling = pathToFileURL(resolve('/opt/pixlstash/dist/renderer-evil/x.html')).href;
    assert.equal(isAllowedNavigation(sibling, BACKEND, unsuffixed), false);
    const inside = pathToFileURL(RENDERER_DIR + 'index.html').href;
    assert.ok(isAllowedNavigation(inside, BACKEND, unsuffixed));
  });
});

/**
 * The wizard and the library app share one window and one preload, so before
 * this the `setup:*` channels stayed reachable from library-served JavaScript
 * after setup finished - and `setup:commit` rewrites the server config,
 * repoints the library and restarts the backend (#1177 item 62). The sending
 * frame's document is the only thing that separates them.
 */
describe('isBundledRendererPage — the setup screen, and only it', () => {
  const isSetup = (target: string) =>
    isBundledRendererPage(target, RENDERER_DIR, 'setup.html');

  it('accepts the wizard page the shell itself loads', () => {
    // The positive direction, and the one that matters most: over-blocking here
    // breaks first-run setup outright, with no way past the wizard.
    assert.ok(isSetup(pathToFileURL(RENDERER_DIR + 'setup.html').href));
    // The dir handed in without its trailing separator must behave the same.
    assert.ok(
      isBundledRendererPage(
        pathToFileURL(RENDERER_DIR + 'setup.html').href,
        RENDERER_DIR.slice(0, -1),
        'setup.html',
      ),
    );
    // A query or hash is still the same document.
    assert.ok(isSetup(pathToFileURL(RENDERER_DIR + 'setup.html').href + '?step=privacy'));
  });

  it('refuses the running library app, which is the actual attack', () => {
    assert.equal(isSetup(BACKEND), false);
    assert.equal(isSetup(BACKEND + 'pictures?page=2'), false);
  });

  it('refuses our other bundled pages', () => {
    // Stricter than isAllowedNavigation on purpose: those may be navigated to,
    // they may not answer for the wizard.
    assert.equal(isSetup(pathToFileURL(RENDERER_DIR + 'index.html').href), false);
    assert.equal(isSetup(pathToFileURL(RENDERER_DIR + 'permissions.html').href), false);
  });

  it('refuses traversal, a prefix-sharing sibling dir, and non-file schemes', () => {
    assert.equal(isSetup(pathToFileURL(RENDERER_DIR + '../renderer-evil/setup.html').href), false);
    assert.equal(isSetup(pathToFileURL(RENDERER_DIR + 'sub/setup.html').href), false);
    assert.equal(
      isSetup(pathToFileURL(resolve(RENDERER_DIR.slice(0, -1) + '-evil') + sep + 'setup.html').href),
      false,
    );
    for (const bad of [
      'file:///etc/setup.html',
      'data:text/html,<b>setup.html</b>',
      'blob:http://127.0.0.1:8723/setup.html',
      'javascript:void 0',
      'not a url',
      '',
    ]) {
      assert.equal(isSetup(bad), false, `${bad} must not pass as the setup screen`);
    }
  });

  // #1206 item 2. `config.ts` folded case on Windows and this file did not, so
  // a `file://` URL whose casing differed from RENDERER_DIR's answered false
  // for the wizard page itself - and requireSetupRenderer then refused EVERY
  // `setup:*` channel, leaving first-run setup with no way through. Windows is
  // where a drive letter's case can differ between two APIs; POSIX is where
  // folding would be the security bug, so both directions are pinned here.
  //
  // Exercised with POSIX-shaped paths and an explicit `platform`, because
  // `resolve()` uses the HOST's semantics: a `C:\` literal would not normalise
  // on the Linux runner. The casing rule is what is under test, not `resolve`.
  describe('path casing follows the platform', () => {
    const UPPER = resolve('/opt/PixlStash/dist/Renderer') + sep;
    const page = pathToFileURL(UPPER + 'setup.html').href;

    it('accepts a differently-cased spelling of the same Windows directory', () => {
      assert.ok(isBundledRendererPage(page, RENDERER_DIR, 'setup.html', 'win32'));
      assert.ok(isAllowedNavigation(page, BACKEND, RENDERER_DIR, 'win32'));
    });

    it('refuses it on POSIX, where that is a different directory', () => {
      assert.equal(isBundledRendererPage(page, RENDERER_DIR, 'setup.html', 'linux'), false);
      assert.equal(isAllowedNavigation(page, BACKEND, RENDERER_DIR, 'linux'), false);
    });

    it('still refuses a prefix-sharing sibling once case is folded', () => {
      const sibling = pathToFileURL(resolve('/opt/pixlstash/dist/RENDERER-evil') + sep + 'setup.html').href;
      assert.equal(isBundledRendererPage(sibling, RENDERER_DIR, 'setup.html', 'win32'), false);
      assert.equal(isAllowedNavigation(sibling, BACKEND, RENDERER_DIR, 'win32'), false);
    });
  });
});

/**
 * Every `ipcMain.handle` in main.ts, as (channel, body) pairs.
 *
 * The gates below are enumerated FROM THE SOURCE rather than from a list
 * written here. A hand-written list is opt-in: the previous version of this
 * file named three handlers by string, so a fourth one added later was gated by
 * nobody and pinned by nothing, which is the same by-omission hole the Python
 * side closed by making "safe by omission" a machine fact
 * (`test_all_routes_declare_access_policy`). Deriving the set means a new
 * `setup:*` or `server:*` channel is covered the moment it exists.
 */
function ipcHandlers(main: string): Array<[string, string]> {
  const found: Array<[string, string]> = [];
  const opener = /ipcMain\.handle\(\s*'([^']+)'/g;
  const starts: Array<[string, number]> = [];
  for (let m = opener.exec(main); m !== null; m = opener.exec(main)) {
    starts.push([m[1], m.index]);
  }
  starts.forEach(([channel, at], i) => {
    const end = i + 1 < starts.length ? starts[i + 1][1] : main.length;
    found.push([channel, main.slice(at, end)]);
  });
  return found;
}

/**
 * `server:setSettings` decides whether this machine listens on the network:
 * it writes `external_server_enabled`, `host: '0.0.0.0'` and `require_ssl`,
 * then restarts the backend. Item 57 validated the payload's shape and left the
 * capability reachable from every renderer page (#1201 F4).
 */
describe('isBackendOrigin — the running app, and only it', () => {
  const isApp = (target: string, current: string | null = BACKEND) =>
    isBackendOrigin(target, current);

  it('accepts the app the backend is actually serving', () => {
    // The direction that must not break: this is the Settings dialog, which is
    // the only legitimate caller (ComputeSection.vue).
    assert.ok(isApp(BACKEND));
    assert.ok(isApp(BACKEND + 'settings'));
    assert.ok(isApp('http://127.0.0.1:8723/pictures?page=2'));
  });

  it('refuses our own bundled pages, which have no business here', () => {
    assert.equal(isApp(pathToFileURL(RENDERER_DIR + 'setup.html').href), false);
    assert.equal(isApp(pathToFileURL(RENDERER_DIR + 'index.html').href), false);
    assert.equal(isApp(pathToFileURL(RENDERER_DIR + 'permissions.html').href), false);
  });

  it('refuses another origin, another port, and an opaque scheme', () => {
    assert.equal(isApp('http://127.0.0.1:9999/'), false);
    assert.equal(isApp('http://localhost:8723/'), false);
    assert.equal(isApp('https://example.invalid/'), false);
    // Origin matches, but the document was authored by the page, not served.
    assert.equal(isApp('blob:http://127.0.0.1:8723/x'), false);
    assert.equal(isApp('http://someone@127.0.0.1:8723/'), false);
    assert.equal(isApp('not a url'), false);
  });

  it('refuses everything before the backend is up', () => {
    // No currentUrl means there is no app yet, so nothing can be it - unlike
    // isAllowedNavigation, which falls back to permitting loopback so an
    // in-flight load is not broken.
    assert.equal(isApp(BACKEND, null), false);
    assert.equal(isApp('http://127.0.0.1:8723/', null), false);
  });
});

describe('IPC gating is complete, not opt-in', () => {
  // __dirname is dist-test/test at run time; the sources are two levels up.
  const main = readFileSync(join(__dirname, '..', '..', 'src', 'main.ts'), 'utf8');
  const handlers = ipcHandlers(main);

  it('finds the handlers at all, so an empty sweep cannot pass vacuously', () => {
    const channels = handlers.map(([c]) => c);
    assert.ok(channels.length > 20, `only found ${channels.length} handlers`);
    for (const required of ['setup:commit', 'server:setSettings', 'backend:setLocation']) {
      assert.ok(channels.includes(required), `${required} not found`);
    }
  });

  it('EVERY setup:* channel is gated on the wizard page', () => {
    const setup = handlers.filter(([c]) => c.startsWith('setup:'));
    assert.ok(setup.length >= 4, `only found ${setup.length} setup:* handlers`);
    for (const [channel, body] of setup) {
      assert.match(
        body,
        new RegExp(`requireSetupRenderer\\(event, '${channel}'\\)`),
        `${channel} must be gated before it does anything`,
      );
    }
  });

  it('EVERY server:* channel is gated on the app window', () => {
    // setSettings turns this machine into a network service; checkPort binds a
    // caller-named port on all interfaces; getSettings returns LAN addresses.
    const server = handlers.filter(([c]) => c.startsWith('server:'));
    assert.ok(server.length >= 3, `only found ${server.length} server:* handlers`);
    for (const [channel, body] of server) {
      assert.match(
        body,
        new RegExp(`requireAppRenderer\\(event, '${channel}'\\)`),
        `${channel} must be gated before it does anything`,
      );
    }
  });

  it('EVERY handler reaching a path sink narrows the path first', () => {
    // Keyed on the sink, not on a list of channels: a new handler that calls
    // changeBackendsLocation or runFirstRunSetup without requireOfferedPath
    // turns this red instead of shipping ungated.
    const sinks = /changeBackendsLocation\(|runFirstRunSetup\(|setBackendsRoot\(/;
    const reaching = handlers.filter(([, body]) => sinks.test(body));
    assert.ok(reaching.length >= 2, `only found ${reaching.length} path-sink handlers`);
    for (const [channel, body] of reaching) {
      assert.match(body, /requireOfferedPath\(/, `${channel} must narrow its path first`);
    }
  });

  it('both guards read the sending frame rather than the webContents', () => {
    // The wizard and the app are the same window behind the same preload, so
    // getURL() alone would report the top document for a frame the page made.
    const guards = main.match(/event\.senderFrame\?\.url \?\? event\.sender\.getURL\(\)/g);
    assert.equal(guards?.length, 2, 'requireSetupRenderer and requireAppRenderer');
    assert.match(main, /isBundledRendererPage\(sender, RENDERER_DIR, 'setup\.html'\)/);
    assert.match(main, /isBackendOrigin\(sender, currentUrl\)/);
  });

  it('leaves the app-renderer startup:* channels open', () => {
    // takePendingTelemetry / takePendingMapping / askQuestion are called BY the
    // library app (useAppConfig.js, SideBar.vue). Gating them would break the
    // upgrade privacy question and the folder-mapping handoff.
    for (const [channel, body] of handlers.filter(([c]) => c.startsWith('startup:'))) {
      assert.doesNotMatch(body, /requireSetupRenderer/, `${channel} must stay open`);
    }
  });
});

describe('redactUrl', () => {
  it('drops userinfo but keeps enough to debug with', () => {
    const withCreds = 'http://someone:example-password@127.0.0.1:8723/x';
    assert.equal(redactUrl(withCreds), 'http://127.0.0.1:8723/x');
    assert.equal(redactUrl('not a url'), '<unparseable URL>');
  });

  it('never echoes a page-authored payload back into the log', () => {
    assert.equal(redactUrl('data:text/plain,' + 'a'.repeat(500)), 'data:text/plain,<redacted>');
    assert.equal(redactUrl('javascript:alert(document.cookie)'), 'javascript:<redacted>');
    assert.equal(redactUrl('about:blank'), 'about:<redacted>');
    // A file: path names a real file and is what a nav failure is debugged with.
    const filePage = pathToFileURL(resolve('/opt/pixlstash/x.html')).href;
    assert.ok(redactUrl(filePage).endsWith('x.html'));
  });
});

// main.ts can't be imported here (it touches `electron` at module load), so pin
// the wiring by source: the popup handler must go through the shared predicate,
// and the string-prefix test that #1020 reported must not come back.
describe('main.ts window-open wiring', () => {
  const source = readFileSync(join(__dirname, '..', '..', 'src', 'main.ts'), 'utf8');

  it('routes setWindowOpenHandler through the shared origin policy', () => {
    assert.match(source, /const openHandler[\s\S]{0,200}?isAllowedTarget\(url\)/);
    assert.match(source, /setWindowOpenHandler\(openHandler\)/);
  });

  it('never classifies a target by the loopback string prefix (#1020)', () => {
    const loopbackPrefix = /startsWith\(\s*['"`]https?:\/\/(127\.0\.0\.1|localhost|\[::1\])/;
    assert.equal(loopbackPrefix.test(source), false);
  });
});
