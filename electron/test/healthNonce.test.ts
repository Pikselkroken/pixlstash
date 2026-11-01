import assert from 'node:assert/strict';
import { createServer, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { after, describe, it } from 'node:test';
import { pollHealth } from '../src/backend/ServerProcess';

/**
 * The window's session cookie and preload bridge go to whatever passes
 * pollHealth, and the backend port is predictable. A squatter that answers
 * /version with a plain 200 must not pass; only the child we started knows the
 * nonce, because it is in that child's environment and nowhere else.
 */
function serve(body: unknown): Promise<{ url: string; server: Server }> {
  return new Promise((resolve) => {
    const server = createServer((_req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify(body));
    });
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo;
      resolve({ url: `http://127.0.0.1:${port}/version`, server });
    });
  });
}

describe('pollHealth trusts only the backend it started', () => {
  const servers: Server[] = [];
  after(() => servers.forEach((s) => s.close()));

  it('resolves when /version echoes this launch nonce', async () => {
    const { url, server } = await serve({ version: '1', desktop_nonce: 'test-nonce' });
    servers.push(server);
    await pollHealth(url, 2_000, 'test-nonce');
  });

  it('refuses a 200 without the nonce, or with the wrong one', async () => {
    for (const body of [{ version: '1' }, { version: '1', desktop_nonce: 'other' }, 'not json']) {
      const { url, server } = await serve(body);
      servers.push(server);
      await assert.rejects(pollHealth(url, 600, 'test-nonce'), /did not become healthy/);
    }
  });
});
