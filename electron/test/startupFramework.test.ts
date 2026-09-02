import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it } from 'node:test';

const rendererDir = join(__dirname, '..', '..', 'src', 'renderer');
const html = readFileSync(join(rendererDir, 'setup.html'), 'utf8');
const script = readFileSync(join(rendererDir, 'setup.js'), 'utf8');
const mainSrc = readFileSync(join(__dirname, '..', '..', 'src', 'main.ts'), 'utf8');

describe('the startup framework', () => {
  it('runs whatever steps main asked for rather than a list of its own', () => {
    assert.match(script, /steps = Array\.isArray\(p\.steps\)/);
    assert.match(mainSrc, /if \(requestedStartupSteps\.length\)/);
    assert.match(
      mainSrc,
      /privacyVariant: 'upgrade'/,
      'a launch that owes only the new privacy question asks exactly that one',
    );
  });

  it('keeps the install step out of the question rail', () => {
    assert.match(script, /step !== 'install'/);
  });

  it('never writes a style attribute, which this window’s CSP refuses', () => {
    assert.match(html, /style-src 'self'/);
    const generated = script.match(/style="/g);
    assert.equal(
      generated,
      null,
      'a style attribute in generated markup is blocked outright: use a class, or set the DOM style property',
    );
  });

  it('keeps the install screen to one line however many packages pip fetches', () => {
    // pip reports a line per download. Keying a row by message grew the screen
    // a line at a time; the message is the note on the one line instead.
    assert.match(script, /const progress = \{ name: '', note: '', fraction: -1 \}/);
    assert.doesNotMatch(script, /PHASES\.push/);
  });

  it('reserves three lines for the installer’s own line, whatever its length', () => {
    // pip's line runs from "numpy" to a wrapped wheel filename, and a row that
    // grows with it walks the bar up and down the screen while you watch.
    const styles = readFileSync(join(rendererDir, 'styles.css'), 'utf8');
    assert.match(styles, /\.phase \.pnote \{[^}]*min-height: 4\.35em/s);
    assert.match(styles, /\.phase \.pnote \{[^}]*-webkit-line-clamp: 3/s);
  });

  it('suggests a folder only for the answer that creates one', () => {
    // A prefilled path with nothing at it is how someone accepts the wrong
    // folder and opens an empty library; "start empty" is the only answer that
    // may name a folder that does not exist yet.
    assert.match(mainSrc, /existingRoot:\s*\n?\s*importedImageRoot && existsSync\(importedImageRoot\)/);
    assert.match(mainSrc, /newRoot: defaultLibraryDir\(\)/);
    assert.match(script, /detectedLegacyIdentitySource \|\| defaults\.existingRoot \|\| ''/);
  });

  it('reads the library while the GPU runtime downloads', () => {
    // Network and disk have nothing to say to each other: the backend starts on
    // the bundled runtime first, without navigating, so hashing and thumbnails
    // run through the download instead of after it.
    const commit = mainSrc.slice(mainSrc.indexOf("'setup:commit'"));
    const startedFirst = commit.indexOf('startWithOverlayFallback(null, false, false)');
    const installed = commit.indexOf('manager.installOverlay(gpu');
    assert.ok(startedFirst > 0, 'the backend has to start before the download');
    assert.ok(startedFirst < installed, 'starting must come first, or nothing overlaps');
  });

  it('parks the privacy answer for the app instead of writing it itself', () => {
    // The answer belongs to the owner's record in a database that does not
    // exist yet, so a commit that wrote it there would have nowhere to write.
    assert.match(mainSrc, /writePendingTelemetry\(choices\?\.telemetry \?\? null\)/);
    assert.match(mainSrc, /ipcMain\.handle\('startup:takePendingTelemetry'/);
  });
});
