import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';
import { pathToFileURL } from 'node:url';
import { join, resolve, sep } from 'node:path';
import { isAllowedNavigation, isBundledRendererPage, redactUrl } from '../src/urlPolicy';

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
});

describe('the setup:* handlers are gated on the sending frame', () => {
  // __dirname is dist-test/test at run time; the sources are two levels up.
  const main = readFileSync(join(__dirname, '..', '..', 'src', 'main.ts'), 'utf8');

  for (const channel of ['setup:probe', 'setup:inspect', 'setup:pickFolder', 'setup:commit']) {
    it(`${channel} refuses a sender that is not the wizard`, () => {
      const start = main.indexOf(`'${channel}'`);
      assert.ok(start >= 0, `${channel} handler not found`);
      const body = main.slice(start, start + 900);
      assert.match(
        body,
        new RegExp(`requireSetupRenderer\\(event, '${channel}'\\)`),
        `${channel} must be gated before it does anything`,
      );
    });
  }

  it('the guard reads the sending frame and compares it to setup.html', () => {
    assert.match(main, /event\.senderFrame\?\.url \?\? event\.sender\.getURL\(\)/);
    assert.match(main, /isBundledRendererPage\(sender, RENDERER_DIR, 'setup\.html'\)/);
  });

  it('leaves the app-renderer startup:* channels open', () => {
    // takePendingTelemetry / takePendingMapping / askQuestion are called BY the
    // library app (useAppConfig.js, SideBar.vue). Gating them would break the
    // upgrade privacy question and the folder-mapping handoff.
    const startup = main.slice(main.indexOf("ipcMain.handle('startup:takePendingTelemetry'"));
    const upToDesktop = startup.slice(0, startup.indexOf("ipcMain.handle('desktop:"));
    assert.doesNotMatch(upToDesktop, /requireSetupRenderer/);
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
