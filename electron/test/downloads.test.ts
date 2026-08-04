import assert from 'node:assert/strict';
import { join } from 'node:path';
import { describe, it } from 'node:test';
import { uniqueDownloadPath } from '../src/downloads';

const DIR = join('/home', 'user', 'Downloads');

describe('automatic download destination', () => {
  it('uses the plain name when nothing is in the way', () => {
    assert.equal(uniqueDownloadPath(DIR, 'holiday.jpg', () => false), join(DIR, 'holiday.jpg'));
  });

  it('suffixes " (n)" instead of overwriting an existing file', () => {
    const taken = new Set([join(DIR, 'holiday.jpg'), join(DIR, 'holiday (1).jpg')]);
    assert.equal(
      uniqueDownloadPath(DIR, 'holiday.jpg', (p) => taken.has(p)),
      join(DIR, 'holiday (2).jpg'),
    );
  });

  it('keeps the extension on the end of the numbered name', () => {
    const taken = new Set([join(DIR, 'clip.tar.gz')]);
    assert.equal(
      uniqueDownloadPath(DIR, 'clip.tar.gz', (p) => taken.has(p)),
      join(DIR, 'clip.tar (1).gz'),
    );
  });

  it('numbers an extensionless name without inventing a dot', () => {
    const taken = new Set([join(DIR, 'media')]);
    assert.equal(uniqueDownloadPath(DIR, 'media', (p) => taken.has(p)), join(DIR, 'media (1)'));
  });

  it('treats a dotfile name as all stem', () => {
    const taken = new Set([join(DIR, '.env')]);
    assert.equal(uniqueDownloadPath(DIR, '.env', (p) => taken.has(p)), join(DIR, '.env (1)'));
  });

  it('gives up after the bounded search rather than looping forever', () => {
    assert.equal(
      uniqueDownloadPath(DIR, 'holiday.jpg', () => true),
      join(DIR, 'holiday (1000).jpg'),
    );
  });
});
