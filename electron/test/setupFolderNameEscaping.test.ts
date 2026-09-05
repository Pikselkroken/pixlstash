import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it } from 'node:test';

const rendererDir = join(__dirname, '..', '..', 'src', 'renderer');

/**
 * The step rail shows the chosen folder's name. That name is the owner's, and
 * a folder may legally be called `<img src=x onerror=...>` on every platform
 * PixlStash runs on, so it must reach the DOM as text and never as markup.
 *
 * It used to be interpolated into a template literal that `answerFor` returned
 * and the caller assigned to `innerHTML` (CodeQL: "DOM text reinterpreted as
 * HTML"). The CSP blocks inline execution, so this was defence in depth rather
 * than a live hole - but it is a privileged renderer and the escape belongs at
 * the sink.
 *
 * Source-text assertions because `setup.js` is a plain renderer script with no
 * module boundary to import, the same way `setupConsentAssets.test.ts` checks
 * this file.
 */
describe('the setup rail never renders the folder name as markup', () => {
  const script = readFileSync(join(rendererDir, 'setup.js'), 'utf8');

  it('never interpolates basename() into a template literal', () => {
    // The regression itself: `${icon}<span>${basename(...)}</span>`.
    assert.doesNotMatch(
      script,
      /`[^`]*\$\{\s*basename\(/,
      'basename() must not be interpolated into a string that reaches innerHTML',
    );
  });

  it('answerFor hands back the icon and the text separately', () => {
    assert.match(
      script,
      /return\s*\{\s*icon,\s*text:\s*basename\(els\.folder\.value\)\s*\}/,
      'the library step must return {icon, text}, not one HTML string',
    );
  });

  it('the label is written with textContent, not innerHTML', () => {
    assert.match(script, /label\.textContent\s*=\s*answer\.text/);
    // The icon is a constant from this file and stays markup; what must not
    // come back is the label going in the same way.
    assert.doesNotMatch(script, /cell\.innerHTML\s*=\s*[^;]*answerFor\(/);
  });
});
