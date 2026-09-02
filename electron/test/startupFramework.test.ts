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

  it('parks the privacy answer for the app instead of writing it itself', () => {
    // The answer belongs to the owner's record in a database that does not
    // exist yet, so a commit that wrote it there would have nowhere to write.
    assert.match(mainSrc, /writePendingTelemetry\(choices\?\.telemetry \?\? null\)/);
    assert.match(mainSrc, /ipcMain\.handle\('startup:takePendingTelemetry'/);
  });
});
