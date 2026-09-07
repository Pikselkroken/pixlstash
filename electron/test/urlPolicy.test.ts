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

    it('does not let percent-encoded padding answer for the page (#1206)', () => {
      // pathKey used to `.trim()` unconditionally, so on POSIX - where `x ` and
      // `x` are two different files - `setup.html%20`, `%0A` and `%09` each
      // came back equal to `setup.html`. Trimming was written for a path
      // someone TYPED, in config.ts; it had no business normalising one parsed
      // out of a URL.
      const setup = pathToFileURL(RENDERER_DIR + 'setup.html').href;
      for (const platform of ['linux', 'win32'] as const) {
        for (const pad of ['%20', '%0A', '%09']) {
          assert.equal(
            isBundledRendererPage(setup + pad, RENDERER_DIR, 'setup.html', platform),
            false,
            `${platform} ${pad}`,
          );
        }
        // The page itself, unpadded, still passes - over-blocking here is the
        // regression that breaks first-run setup outright.
        assert.ok(isBundledRendererPage(setup, RENDERER_DIR, 'setup.html', platform));
      }
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
 *
 * #1206 item 10: the pattern was `ipcMain.handle(\s*'`, which is five ways to
 * be invisible to it - `handleOnce`, `on`/`once`, a double-quoted or backtick
 * name, a computed one, and a concatenated one. Any of them could be added and
 * this file stayed green. The spellings are all read now; a name that cannot be
 * read out of the source at all is made loud rather than skipped (`every
 * handler opener is read` below).
 *
 * Deriving the set is only half of it. Enumerating what the set must SATISFY is
 * the other half - see DELIBERATELY_OPEN.
 */
/**
 * Whitespace or a comment. TypeScript allows either between the method name
 * and its `(`, so a handler registered with a block or line comment sitting in
 * that gap is invisible to BOTH patterns below - the opener count would agree
 * with the named count and nothing would go red. Same silent bypass as item
 * 10, one token further along. (Not written out as an example here: the
 * example's own comment terminator would end this one.)
 */
const OPENER_GAP = '(?:\\s|/\\*[\\s\\S]*?\\*/|//[^\\n]*\\n)*';
/**
 * Every way `ipcMain` takes a channel. `on`/`once` are the ordinary handler
 * shape for a synchronous call (`e.returnValue = …`) and register a channel
 * exactly as `handle` does - main.ts has none today, which is precisely why
 * they belong here: an unswept spelling is only invisible until someone uses
 * it. `once` is listed before `on` so the alternation cannot stop after two
 * characters of `once`.
 */
const IPC_OPENER = `ipcMain\\.(?:handle(?:Once)?|once|on)${OPENER_GAP}\\(`;
/**
 * A channel named by a WHOLE string literal: the quoted run must reach the
 * comma that ends the argument, and may not contain `$`.
 *
 * Both halves are load-bearing. `ipcMain.handle('server' + ':evil', …)` used to
 * be read as the channel `"server"` - which does not start with `"server:"`, so
 * it skipped the gate assertion, while still counting as one named handler
 * against one opener, so it passed the count test too. Silently ungated in
 * both directions. Requiring the comma means a concatenation is no longer
 * *named*, and the count test below then reports it. `$` is excluded for the
 * same reason one token further: `` `server:${x}` `` would otherwise be read
 * as the literal channel `server:${x}`.
 */
const NAMED_HANDLE = new RegExp(`${IPC_OPENER}\\s*(['"\`])([^'"\`$]+)\\1\\s*,`);
/** The same opener with the channel name left unconstrained. */
const ANY_HANDLE = new RegExp(IPC_OPENER);

function ipcHandlers(main: string): Array<[string, string]> {
  const found: Array<[string, string]> = [];
  const opener = new RegExp(NAMED_HANDLE.source, 'g');
  const starts: Array<[string, number]> = [];
  for (let m = opener.exec(main); m !== null; m = opener.exec(main)) {
    starts.push([m[2], m.index]);
  }
  starts.forEach(([channel, at], i) => {
    const end = i + 1 < starts.length ? starts[i + 1][1] : main.length;
    found.push([channel, main.slice(at, end)]);
  });
  return found;
}

/**
 * The channels that carry NO renderer gate, and the reason each one is safe
 * without one.
 *
 * This is what turns the sweep below from a PREFIX test into an enumeration.
 * Before it, the only gate assertions were `setup:*` and `server:*`; the other
 * two dozen channels were checked by nothing, and nothing recorded that this
 * was a decision rather than an oversight - so a `desktop:runScript` added
 * tomorrow would ship ungated and green. Same by-omission hole, and the same
 * answer, as the Python side's `test_all_routes_declare_access_policy`: a
 * channel in neither column fails the build.
 *
 * A reason here is not a waiver. It is the sentence a reviewer checks, and
 * writing one is meant to be the moment someone notices there isn't a good one.
 * The standing floor under all of them is `isAllowedNavigation`: no third-party
 * page can be loaded into this window at all, so "open" means open to our own
 * splash, wizard and library pages, not to the web.
 */
const DELIBERATELY_OPEN: Record<string, string> = {
  // ---- Read-only reports about this install ----
  'app:bootstrap': 'read-only: version, detected hardware, bundled/active accel',
  'permissions:request': 'read-only: hands back the repair report main already built',
  'accel:list': 'read-only: which overlays are installed and which is active',
  'backend:getLocation': 'read-only: where overlays are installed, and the default',
  'desktop:getPrefs': "read-only: the shell's own prefs (tray-on-close, shim state)",

  // ---- Registered only while something is waiting for the answer ----
  'permissions:resolve':
    'registered only while the permission screen waits, and removeHandler()d the ' +
    'moment it answers - see "exists only while the repair screen waits" below',

  // ---- Hand back an answer this process parked for the page asking ----
  'startup:takePendingTelemetry': 'take-once handback of an answer main parked; writes nothing',
  'startup:takePendingMapping': 'take-once handback of an answer main parked; writes nothing',

  // ---- No argument: the target is one PixlStash chose itself ----
  'desktop:openLibraryFolder': 'no argument: opens the library folder main already knows',
  'desktop:showLogs': 'no argument: opens our own log file',
  'window:minimize': 'frameless-window title-bar control; changes no state but the window',
  'window:toggleMaximize': 'frameless-window title-bar control; changes no state but the window',
  'window:close': 'frameless-window title-bar control; changes no state but the window',

  // ---- The person at the keyboard is the gate ----
  'media:beginSaveAs':
    'the destination comes from a native Save dialog, never from the caller; the ' +
    'pending save is keyed to the sending webContents',
  'media:completeSaveAs': 'writes only to a path beginSaveAs got from the dialog, same sender',
  'media:cancelSaveAs': 'drops a pending save, same-sender checked; touches no file',
  'media:copyPng': 'writes the clipboard from bytes the calling page already holds',
  'backend:pickLocation':
    'opens a folder dialog; the answer is only OFFERED, and backend:setLocation ' +
    'independently re-checks it through requireOfferedPath',

  // ---- Shell preferences, no library and no listener ----
  'desktop:setPrefs': "the shell's own prefs (tray-on-close, the `pixlstash` shim); no path, no listener",

  // ---- Both audiences call these, so neither gate fits: the ARGUMENT is narrowed ----
  // The first-run wizard (a file:// page, electron/src/renderer/setup.js) and
  // the app's Settings (ComputeSection.vue, backend-served) both call all four.
  // requireAppRenderer would break first-run setup outright; requireSetupRenderer
  // would break Settings. The value is what is checked instead, at the boundary.
  'backend:setLocation': 'wizard AND Settings both call it; narrowed by requireOfferedPath',
  'accel:install': 'wizard AND Settings both call it; narrowed by requireAccel',
  'accel:use': 'wizard AND Settings both call it; narrowed by requireAccel',
  'accel:remove': 'wizard AND Settings both call it; narrowed by requireAccel',
};

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

  // A channel name built at run time cannot be read out of the source, so it
  // cannot be checked for a gate either. Refuse it here rather than let the
  // sweep walk past it: this is the difference between a guardrail with a gap
  // and one that says where the gap is.
  it('every handler opener is read, so a computed channel name cannot hide', () => {
    const total = main.match(new RegExp(ANY_HANDLE.source, 'g'))?.length ?? 0;
    assert.equal(
      handlers.length,
      total,
      `${total - handlers.length} ipcMain registration(s) do not name their channel ` +
        'with one whole string literal - a computed name, a template, or a ' +
        'concatenation - so nothing here can tell whether they are gated. ' +
        'Give the channel a literal name.',
    );
  });

  it('reads handleOnce, on, once, double quotes and backticks', () => {
    const synthetic = [
      "ipcMain.handleOnce('once:channel', () => 1);",
      'ipcMain.handle("double:quoted", () => 2);',
      'ipcMain.handle(`backticked`, () => 3);',
      // The synchronous handler shape. main.ts has none, so nothing here would
      // go red if the sweep stopped reading it - which is the whole point of
      // pinning it: it must be seen the day one arrives, not the day after.
      "ipcMain.on('sync:channel', (e) => { e.returnValue = 1; });",
      "ipcMain.once('sync:onceChannel', (e) => { e.returnValue = 2; });",
    ].join('\n');
    assert.deepEqual(
      ipcHandlers(synthetic).map(([c]) => c),
      ['once:channel', 'double:quoted', 'backticked', 'sync:channel', 'sync:onceChannel'],
    );
    // A computed name is an opener the sweep cannot name: zero handlers found,
    // one opener present, which is exactly the discrepancy the count test reads.
    const computed = 'ipcMain.handle(CHANNEL, () => 4);';
    assert.equal(ipcHandlers(computed).length, 0);
    assert.equal(computed.match(new RegExp(ANY_HANDLE.source, 'g'))?.length, 1);
  });

  it('refuses a channel name that is only PART of the argument', () => {
    // The quiet one. `'server' + ':evil'` was read as the channel "server",
    // which does not start with "server:" - so it skipped the gate assertion -
    // while still counting one named handler against one opener, so it passed
    // the count test too. Ungated in both directions and green. Now it is not
    // named at all, and the count test reports it like any other computed name.
    for (const built of [
      "ipcMain.handle('server' + ':evil', () => 1);",
      'ipcMain.handle("desktop" + ":runScript", () => 2);',
      'ipcMain.handle(`server:${x}`, () => 3);',
    ]) {
      assert.deepEqual(ipcHandlers(built), [], built);
      assert.equal(built.match(new RegExp(ANY_HANDLE.source, 'g'))?.length, 1, built);
    }
  });

  it('reads an opener with a comment before its paren', () => {
    // TypeScript allows a comment between the method name and `(`. Before this
    // it hid the handler from the named sweep AND from the opener count, so
    // the two agreed and the "cannot hide" test above stayed green - a silent
    // bypass rather than a loud one. Built from pieces so this file does not
    // contain a comment terminator that would end its own.
    const block = '/' + '* hidden *' + '/';
    const commented = [
      `ipcMain.handle${block}('block:commented', () => 1);`,
      'ipcMain.handleOnce // trailing\n(\'line:commented\', () => 2);',
    ].join('\n');
    assert.deepEqual(
      ipcHandlers(commented).map(([c]) => c),
      ['block:commented', 'line:commented'],
    );
    // And a computed name hidden the same way is still counted as an opener,
    // so it fails loudly instead of vanishing.
    const hidden = `ipcMain.handle${block}(CHANNEL, () => 3);`;
    assert.equal(ipcHandlers(hidden).length, 0);
    assert.equal(hidden.match(new RegExp(ANY_HANDLE.source, 'g'))?.length, 1);
  });

  /** The gate a handler declares for ITS OWN channel, not for a neighbour's. */
  const gateFor = (channel: string) =>
    new RegExp(`require(?:Setup|App)Renderer\\(event, '${channel}'\\)`);

  it('EVERY channel is gated or written down as deliberately open', () => {
    const ungated = handlers
      .filter(([channel, body]) => !gateFor(channel).test(body))
      .map(([channel]) => channel)
      .filter((channel) => !(channel in DELIBERATELY_OPEN));
    assert.deepEqual(
      ungated,
      [],
      `${ungated.length} IPC channel(s) carry no requireSetupRenderer/requireAppRenderer ` +
        'gate and are not listed in DELIBERATELY_OPEN. Gate them, or add each to the ' +
        'map with the one-line reason it is safe without one.',
    );
  });

  it('the deliberately-open list stays honest: no stale or contradicted entry', () => {
    // Without this the map is a place to park a channel and forget it: a
    // renamed channel would leave a reason behind that guards nothing, and a
    // channel later gated would keep an entry saying it is not.
    const byChannel = new Map(handlers);
    for (const [channel, reason] of Object.entries(DELIBERATELY_OPEN)) {
      const body = byChannel.get(channel);
      assert.ok(body, `DELIBERATELY_OPEN names ${channel}, which main.ts no longer registers`);
      assert.ok(reason.length > 20, `${channel} needs a real reason, not "${reason}"`);
      assert.doesNotMatch(
        body,
        gateFor(channel),
        `${channel} IS gated now - drop its DELIBERATELY_OPEN entry`,
      );
    }
  });

  it('permissions:resolve exists only while the repair screen waits', () => {
    // Its DELIBERATELY_OPEN reason is the registration window itself, so pin
    // that window rather than take the reason's word for it: registered inside
    // the promise the screen is awaiting, and removed on every settle path
    // (answered, or the window closed).
    assert.match(main, /ipcMain\.removeHandler\('permissions:resolve'\)/);
    assert.match(main, /const settle = \(accepted: boolean\) => \{[\s\S]{0,200}?removeHandler/);
  });

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

  it('never gates a startup:* channel on the WIZARD page', () => {
    // takePendingTelemetry / takePendingMapping / askQuestion are called BY the
    // library app (useAppConfig.js, SideBar.vue). Gating them on setup.html
    // would break the upgrade privacy question and the folder-mapping handoff.
    for (const [channel, body] of handlers.filter(([c]) => c.startsWith('startup:'))) {
      assert.doesNotMatch(body, /requireSetupRenderer/, `${channel} must stay open`);
    }
  });

  it('gates startup:askQuestion on the app window (#1206 item 4)', () => {
    // It writes nothing, but it replaces the document with the full-screen
    // wizard - so an ungated one let any page throw the owner out of the
    // library and lose unsaved work. The two `takePending*` channels stay open:
    // they only hand back an answer this process parked for the page asking.
    const [, askQuestion] = handlers.find(([c]) => c === 'startup:askQuestion') ?? [];
    assert.ok(askQuestion, 'startup:askQuestion not found');
    assert.match(askQuestion, /requireAppRenderer\(event, 'startup:askQuestion'\)/);
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
