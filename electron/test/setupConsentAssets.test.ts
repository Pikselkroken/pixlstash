import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it } from 'node:test';

const rendererDir = join(__dirname, '..', '..', 'src', 'renderer');

describe('legacy identity consent assets', () => {
  it('starts unchecked and wires checked state to the selected panel treatment', () => {
    const html = readFileSync(join(rendererDir, 'setup.html'), 'utf8');
    const script = readFileSync(join(rendererDir, 'setup.js'), 'utf8');
    const styles = readFileSync(join(rendererDir, 'styles.css'), 'utf8');

    const checkbox = html.match(/<input id="importLegacyIdentity"[^>]*>/)?.[0];
    assert.ok(checkbox, 'the explicit identity-import consent must exist');
    assert.doesNotMatch(checkbox, /\bchecked\b/, 'consent must default to declined');
    assert.match(script, /classList\.toggle\(\s*'panel--selected'/);
    assert.match(script, /importLegacyIdentity\.checked/);
    assert.match(script, /addEventListener\('change', updateLegacyIdentitySelected\)/);
    assert.match(styles, /\.panel--selected\s*{/);
    assert.match(styles, /\.identity-consent:has\(input:focus-visible\)/);
  });
});
