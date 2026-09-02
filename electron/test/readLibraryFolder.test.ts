import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { readLibraryFolder } from '../src/setup/ReadLibraryFolder';

const NO_SLEEP = async () => {};

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe('reading the library folder during the runtime download', () => {
  it('follows the read to the end and hands back the task the wizard resumes', async () => {
    const seen: string[] = [];
    const statuses = [
      { status: 'running', processed: 4, total: 12, progress: 0.33 },
      { status: 'running', processed: 9, total: 12, progress: 0.75 },
      { status: 'completed', processed: 12, total: 12, progress: 1 },
    ];
    const fetcher = async (url: string) => {
      seen.push(url);
      if (url.endsWith('/folder-structure/read')) return jsonResponse({ task_id: 'task-7' });
      return jsonResponse(statuses.shift());
    };
    const progress: number[] = [];

    const taskId = await readLibraryFolder(
      fetcher,
      'http://127.0.0.1:9999',
      'session-token',
      '/home/me/Pictures',
      (p) => progress.push(p.processed),
      NO_SLEEP,
    );

    assert.equal(taskId, 'task-7');
    assert.deepEqual(progress, [4, 9, 12], 'every poll feeds the screen its count');
    assert.match(seen[1], /task_id=task-7/);
    // Every route is under /api/v1 - the frontend client's own API_PREFIX and
    // what the authz registry declares. Without it the call 404s in silence.
    assert.ok(
      seen.every((url) => url.includes('/api/v1/folder-structure/read')),
      `both calls must carry the API prefix: ${seen.join(', ')}`,
    );
  });

  it('sends the loopback session cookie, or the backend would refuse it', async () => {
    let headers: Record<string, string> = {};
    const fetcher = async (url: string, init?: RequestInit) => {
      headers = (init?.headers as Record<string, string>) ?? {};
      if (url.endsWith('/folder-structure/read')) return jsonResponse({ task_id: 't' });
      return jsonResponse({ status: 'completed' });
    };

    await readLibraryFolder(fetcher, 'http://127.0.0.1:9999', 'abc123', '/p', () => {}, NO_SLEEP);

    assert.match(headers.cookie, /session_id=abc123/);
  });

  it('reads without matching, so it cannot name the active library’s entities', async () => {
    let body: unknown = null;
    const fetcher = async (url: string, init?: RequestInit) => {
      if (url.endsWith('/folder-structure/read')) {
        body = JSON.parse(String(init?.body));
        return jsonResponse({ task_id: 't' });
      }
      return jsonResponse({ status: 'completed' });
    };

    await readLibraryFolder(fetcher, 'http://127.0.0.1:9999', 's', '/p', () => {}, NO_SLEEP);

    assert.deepEqual(body, { path: '/p', match_existing: false });
  });

  it('gives up quietly when the read cannot start, so setup carries on', async () => {
    // A failed read costs the overlap and nothing else: the app reads the
    // folder itself, exactly as it did before any of this existed.
    const fetcher = async () => jsonResponse({}, false, 500);

    const reports: Array<{ failed?: boolean }> = [];
    const taskId = await readLibraryFolder(
      fetcher,
      'http://127.0.0.1:9999',
      's',
      '/p',
      (p) => reports.push(p),
      NO_SLEEP,
    );

    assert.equal(taskId, null);
    assert.equal(
      reports.at(-1)?.failed,
      true,
      'the line it was going to fill has to say it is not filling',
    );
  });

  it('keeps a cancelled read: the wizard shows what was found', async () => {
    const fetcher = async (url: string) =>
      url.endsWith('/folder-structure/read')
        ? jsonResponse({ task_id: 'half' })
        : jsonResponse({ status: 'cancelled', processed: 3, total: 12 });

    const taskId = await readLibraryFolder(
      fetcher,
      'http://127.0.0.1:9999',
      's',
      '/p',
      () => {},
      NO_SLEEP,
    );

    assert.equal(taskId, 'half');
  });

  it('stops waiting on a read that has gone silent', async () => {
    let clock = 0;
    const fetcher = async (url: string) =>
      url.endsWith('/folder-structure/read')
        ? jsonResponse({ task_id: 'stuck' })
        : jsonResponse({ status: 'running', processed: 1, total: 99 });

    const taskId = await readLibraryFolder(
      fetcher,
      'http://127.0.0.1:9999',
      's',
      '/p',
      () => {},
      async () => {
        clock += 60_000;
      },
      () => clock,
    );

    assert.equal(taskId, null, 'a read that never ends must not hold up the app');
  });
});
