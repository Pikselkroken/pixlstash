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

  it('has a fixed set of lines, however many packages pip fetches', () => {
    // pip reports a line per download. Keying a row by message grew the screen
    // a line at a time; the lines are decided from the answers up front and a
    // message is the note on the line it belongs to.
    assert.match(script, /function planLines\(useGpu\)/);
    assert.match(script, /setLine\('runtime', \{/);
    assert.doesNotMatch(script, /lines\.push\(\{ id: message/);
  });

  it('shows the reading and the download as two lines, not one overwriting the other', () => {
    // The whole point of starting the server before the download is that they
    // run at once; one line replacing the other hides it.
    assert.match(script, /id: 'server',\s*\n?\s*name: 'Preparing your library'/);
    assert.match(script, /p\.phase === 'reading'/);
    assert.match(mainSrc, /sendPhase\(\{ phase: 'reading' \}\)/);
  });

  it('reserves the installer’s line whatever its length', () => {
    // pip's line runs from "numpy" to a wrapped wheel filename, and a row that
    // grows with it walks the bar up and down the screen while you watch.
    const styles = readFileSync(join(rendererDir, 'styles.css'), 'utf8');
    assert.match(styles, /\.phase--notes \.pnote \{[^}]*min-height: 2\.9em/s);
    assert.match(styles, /\.phase \.pnote \{[^}]*-webkit-line-clamp: 2/s);
  });

  it('suggests a folder only for the answer that creates one', () => {
    // A prefilled path with nothing at it is how someone accepts the wrong
    // folder and opens an empty library; "start empty" is the only answer that
    // may name a folder that does not exist yet.
    assert.match(mainSrc, /existingRoot:\s*\n?\s*importedImageRoot && existsSync\(importedImageRoot\)/);
    assert.match(mainSrc, /newRoot: defaultLibraryDir\(\)/);
    assert.match(script, /detectedLegacyIdentitySource \|\| defaults\.existingRoot \|\| ''/);
  });

  it('offers the permission repair from setup, as boot does', () => {
    // The backend refuses a group-writable library folder. boot() knows to
    // offer the repair; setup starts the backend itself, and without this the
    // refusal came back as a bare rejection nobody could act on.
    assert.match(mainSrc, /async function startFromSetup/);
    assert.match(mainSrc, /startBackend: startFromSetup/);
    assert.match(mainSrc, /if \(!isPermissionRepairRequired\(caught\)\) throw caught;/);
  });

  it('keeps a failed setup on the step that failed', () => {
    // Dropping back to the first question hid the message on a screen that was
    // no longer shown: an unexplained trip back to the beginning.
    assert.match(script, /failed = true;/);
    assert.doesNotMatch(script, /showError\([^)]*\);\s*\n\s*busy = false;\s*\n\s*go\(0\);/);
  });

  it('never draws unknown progress as a partial fill', () => {
    // A 40%-wide chunk of the determinate fill's own colour, height and radius
    // is how "62% done" is drawn; sliding it right made it "stuck at 95%".
    // Unknown progress gets a hairline across the track and no fill at all.
    const styles = readFileSync(join(rendererDir, 'styles.css'), 'utf8');
    assert.match(styles, /\.meter--unknown \.meter-fill \{[^}]*height: 2px/s);
    assert.match(script, /meter\.classList\.add\('meter--unknown'\)/);
    assert.doesNotMatch(script, /barfill indeterminate/);
  });

  it('states the number each row is about', () => {
    // The widest object on the screen was also the least informative: the read
    // knows its folder counts and pip names every wheel's size.
    assert.match(script, /of \$\{count\(p\.total\)\} \$\{noun\}/);
    assert.match(script, /humanBytes\(done\)\} of \$\{humanBytes\(total\)/);
    assert.match(mainSrc.replace(/\s+/g, ' '), /bytesDone|install:progress/);
  });

  it('groups a meter with the label above it, not the row below it', () => {
    const styles = readFileSync(join(rendererDir, 'styles.css'), 'utf8');
    const phases = styles.slice(styles.indexOf('.phases {'));
    const between = Number(phases.match(/gap: (\d+)px/)?.[1]);
    const inside = Number(styles.match(/\.phase \{[^}]*row-gap: (\d+)px/s)?.[1]);
    assert.ok(
      between > inside,
      `the gap between activities (${between}px) must exceed the gap inside one (${inside}px)`,
    );
  });

  it('parks the privacy answer for the app instead of writing it itself', () => {
    // The answer belongs to the owner's record in a database that does not
    // exist yet, so a commit that wrote it there would have nowhere to write.
    assert.match(mainSrc, /writePendingTelemetry\(choices\?\.telemetry \?\? null\)/);
    assert.match(mainSrc, /ipcMain\.handle\('startup:takePendingTelemetry'/);
  });
});
