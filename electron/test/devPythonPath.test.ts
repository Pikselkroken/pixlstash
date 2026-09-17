import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { devPythonPath } from '../src/backend/ServerProcess';

describe('devPythonPath — a dev launch imports its own checkout', () => {
  it('puts the checkout first when nothing is set', () => {
    assert.equal(devPythonPath('/home/me/branch-a', undefined, ':'), '/home/me/branch-a');
    assert.equal(devPythonPath('/home/me/branch-a', '', ':'), '/home/me/branch-a');
  });

  it('keeps the shell\'s entries after the checkout', () => {
    assert.equal(
      devPythonPath('/home/me/branch-a', '/home/me/extra:/opt/lib', ':'),
      '/home/me/branch-a:/home/me/extra:/opt/lib',
    );
  });

  it('moves another checkout the shell put first behind this one', () => {
    // The failure this exists for: a PYTHONPATH (or a venv) naming a different
    // branch must not win over the checkout the app was built from.
    assert.equal(
      devPythonPath('/home/me/branch-a', '/home/me/branch-b:/home/me/branch-a', ':'),
      '/home/me/branch-a:/home/me/branch-b',
    );
  });

  it('uses the platform separator on Windows', () => {
    assert.equal(
      devPythonPath('C:\\src\\branch-a', 'C:\\lib', ';'),
      'C:\\src\\branch-a;C:\\lib',
    );
  });

});
