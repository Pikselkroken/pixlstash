// The `pixlstash-mcp` shim: how an MCP client finds the server.
//
// A client spawns `pixlstash-mcp` by name, with no shell and no login profile,
// so it has to be a real file on PATH. The desktop build already carries a
// working interpreter; this puts a one-line forwarder in front of it rather
// than asking the user to install the Python package into an interpreter of
// their own.
//
// The protocol is newline-delimited JSON-RPC over stdin and stdout, so the
// thing these tests care most about is that nothing can end up writing to
// stdout besides the server itself.

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { existsSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { mcpShimPath, mcpShimScript, parseMcpArgs, syncMcpShim } from '../src/cliShim';

const CFG = '/home/me/.config/pixlstash-desktop/server-config.json';

describe('parseMcpArgs - deciding between a window and an MCP run', () => {
  it('a packaged launch with no arguments opens a window', () => {
    assert.equal(parseMcpArgs(['/opt/PixlStash/pixlstash']), null);
  });

  it('everything after the marker belongs to the MCP server', () => {
    const argv = ['/opt/PixlStash/pixlstash', 'mcp', '--url', 'http://127.0.0.1:9537'];
    assert.deepEqual(parseMcpArgs(argv), ['--url', 'http://127.0.0.1:9537']);
  });

  it('a bare marker is an MCP run with no arguments, not a window', () => {
    assert.deepEqual(parseMcpArgs(['/opt/PixlStash/pixlstash', 'mcp']), []);
  });

  it("the executable's own path is never mistaken for the marker", () => {
    // Searched from index 1, so a user who installed into a directory called
    // `mcp` still gets a window.
    assert.equal(parseMcpArgs(['/home/me/mcp']), null);
  });
});

describe('mcpShimScript - what the forwarder runs', () => {
  it('goes through the launcher on unix, which is the durable name', () => {
    // An AppImage is mounted at a different random path every launch, so the
    // interpreter inside it cannot be named ahead of time; the .AppImage can.
    const script = mcpShimScript('/home/me/Apps/PixlStash.AppImage', '/home/me/.config/pixlstash-desktop/server-config.json');
    assert.match(script, /^#!\/bin\/sh\n/);
    assert.match(script, /exec '\/home\/me\/Apps\/PixlStash\.AppImage' mcp /);
    // The desktop keeps its own server-config.json; reading the platform
    // default instead got the wrong port and the wrong scheme, which is what
    // made every tool call fail against an https listener.
    assert.ok(script.includes(`--server-config '${CFG}'`));
    assert.match(script, /"\$@"\n$/);
  });

  it('goes straight to the interpreter on Windows, whose launcher has no console', () => {
    const script = mcpShimScript('ignored', 'C:\\cfg\\server-config.json', 'C:\\Program Files\\PixlStash\\python.exe');
    assert.ok(script.includes('-m pixlstash.mcp_server --server-config'));
    assert.ok(script.includes('"C:\\cfg\\server-config.json" %*'));
    assert.ok(script.includes('"C:\\Program Files\\PixlStash\\python.exe"'));
    // CRLF, or cmd.exe mis-parses it.
    assert.ok(script.includes('\r\n'));
  });

  it('names no URL, because the server reads the configured port itself', () => {
    // Baking one in is how the dialog shipped a dead ephemeral port once.
    assert.ok(!mcpShimScript('/opt/PixlStash/pixlstash', '/home/me/.config/pixlstash-desktop/server-config.json').includes('--url'));
    assert.ok(!mcpShimScript('x', 'C:\\cfg.json', 'C:\\python.exe').includes('--url'));
  });

  it("quotes a path with a space, and a shell quote inside a user's home", () => {
    const script = mcpShimScript("/home/o'brien/My Apps/PixlStash.AppImage", '/home/me/.config/pixlstash-desktop/server-config.json');
    assert.match(script, /exec '\/home\/o'\\''brien\/My Apps\/PixlStash\.AppImage' mcp/);
  });
});

describe('syncMcpShim - installing and removing it', () => {
  const shimIn = (dir: string) => join(dir, 'pixlstash-mcp');

  it('writes an executable forwarder and removes it again', () => {
    const dir = mkdtempSync(join(tmpdir(), 'pixlstash-mcp-shim-'));
    const path = shimIn(dir);

    assert.equal(syncMcpShim(true, '/opt/PixlStash/pixlstash', CFG, path), true);
    assert.ok(existsSync(path));
    assert.match(readFileSync(path, 'utf8'), / mcp --server-config /);
    // Without the execute bit the client's spawn fails with EACCES.
    assert.equal(statSync(path).mode & 0o111, 0o111);

    assert.equal(syncMcpShim(false, '/opt/PixlStash/pixlstash', CFG, path), false);
    assert.equal(existsSync(path), false);
  });

  it("refuses to overwrite a pixlstash-mcp the user put there themselves", () => {
    const dir = mkdtempSync(join(tmpdir(), 'pixlstash-mcp-shim-'));
    const path = shimIn(dir);
    writeFileSync(path, '#!/bin/sh\n# mine, from my own venv\n');

    assert.equal(syncMcpShim(true, '/opt/PixlStash/pixlstash', CFG, path), false);
    assert.match(readFileSync(path, 'utf8'), /mine, from my own venv/);

    // And disabling leaves it alone too, rather than deleting their file.
    assert.equal(syncMcpShim(false, '/opt/PixlStash/pixlstash', CFG, path), false);
    assert.ok(existsSync(path));
  });

  it('rewrites the target, so moving the AppImage repairs the shim', () => {
    const dir = mkdtempSync(join(tmpdir(), 'pixlstash-mcp-shim-'));
    const path = shimIn(dir);

    syncMcpShim(true, '/old/PixlStash.AppImage', CFG, path);
    syncMcpShim(true, '/new/PixlStash.AppImage', CFG, path);
    const script = readFileSync(path, 'utf8');
    assert.ok(script.includes('/new/PixlStash.AppImage'));
    assert.ok(!script.includes('/old/PixlStash.AppImage'));
  });
});

describe('mcpShimPath - where it goes', () => {
  it('sits beside the CLI shim, so one PATH entry serves both', () => {
    assert.equal(mcpShimPath('/home/me', 'linux'), '/home/me/.local/bin/pixlstash-mcp');
  });

  it('is a .cmd on Windows, which is what makes it runnable there', () => {
    const path = mcpShimPath('C:\\Users\\me', 'win32');
    assert.ok(path.endsWith('pixlstash-mcp.cmd'));
  });
});
