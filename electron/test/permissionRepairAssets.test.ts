import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it } from 'node:test';

const rendererDir = join(__dirname, '..', '..', 'src', 'renderer');

describe('permission repair screen assets', () => {
  it('is one centred column, not the setup screen’s two-column row', () => {
    // It carries `setup` for the shared chrome, and `setup` is a row. Left as
    // one, the 600px cap was shared between the header, the panel and the
    // buttons: the title ran off the left edge and "Fix it" was a sliver.
    const styles = readFileSync(join(rendererDir, 'styles.css'), 'utf8');
    const rule = styles.slice(styles.indexOf('.app.repair {'));
    assert.match(rule.slice(0, rule.indexOf('}')), /flex-direction: column/);
  });

  it('renders the backend report and reports exactly one answer back to main', () => {
    const html = readFileSync(join(rendererDir, 'permissions.html'), 'utf8');
    const script = readFileSync(join(rendererDir, 'permissions.js'), 'utf8');
    const styles = readFileSync(join(rendererDir, 'styles.css'), 'utf8');

    // The script drives the page entirely by id; a rename on either side leaves
    // the screen inert with no way to answer.
    for (const id of ['issues', 'fix', 'quit', 'error']) {
      assert.match(html, new RegExp(`id="${id}"`), `permissions.html must keep #${id}`);
      assert.match(script, new RegExp(`getElementById\\('${id}'\\)`));
    }
    assert.match(script, /api\.permissionRepairRequest\(\)/);
    assert.match(script, /api\.resolvePermissionRepair\(accepted\)/);
    // Refusal must stay reachable without the mouse, and both buttons must
    // answer at most once — main removes its handler after the first reply.
    assert.match(script, /event\.key === 'Escape'/);
    assert.match(script, /if \(answered\) return;/);
    assert.match(styles, /\.app\.repair\s*{/);
    assert.match(styles, /\.repair-item\s*{/);
  });
});
