import { spawn, ChildProcess, execFile } from 'node:child_process';
import { createServer } from 'node:net';
import { get as httpGet } from 'node:http';
import { createWriteStream, WriteStream } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
import { join, dirname, delimiter } from 'node:path';
import { existsSync } from 'node:fs';
import { bundledInterpreter, isDevBackend, serverConfigPath, serverLogPath } from '../config';

export interface RunningServer {
  url: string;
  port: number;
  /** The pre-authenticated loopback session token to inject as a cookie. */
  sessionToken: string;
}

/**
 * In dev (PIXLSTASH_DESKTOP_DEV=1) we run against a local interpreter — the
 * repo's .venv by default, or PIXLSTASH_DEV_BACKEND if set. This is the loop
 * used to iterate on the shell without building the bundled runtime.
 */
export function devInterpreter(): string {
  if (process.env.PIXLSTASH_DEV_BACKEND) return process.env.PIXLSTASH_DEV_BACKEND;
  // electron/dist/backend → repo root is three levels up.
  const repoRoot = join(__dirname, '..', '..', '..');
  const unix = join(repoRoot, '.venv', 'bin', 'python');
  const win = join(repoRoot, '.venv', 'Scripts', 'python.exe');
  return process.platform === 'win32' ? win : unix;
}

async function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address();
      const port = typeof addr === 'object' && addr ? addr.port : 0;
      srv.close(() => resolve(port));
    });
  });
}

function pollHealth(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = httpGet(url, (res) => {
        res.resume();
        if (res.statusCode === 200) {
          resolve();
        } else {
          retry();
        }
      });
      req.on('error', retry);
      req.setTimeout(2000, () => req.destroy());
    };
    const retry = () => {
      if (Date.now() > deadline) {
        reject(new Error(`Server did not become healthy within ${timeoutMs}ms`));
        return;
      }
      setTimeout(attempt, 500);
    };
    attempt();
  });
}

/**
 * A rolling tail of a process's combined stdout+stderr, capped in size. When the
 * backend dies during startup we replay this so the user sees the real cause
 * (e.g. a failed startup check) instead of a bare health-check timeout.
 */
class OutputTail {
  private static readonly MAX_CHARS = 4000;
  private buf = '';

  push(chunk: Buffer | string): void {
    this.buf += chunk.toString();
    if (this.buf.length > OutputTail.MAX_CHARS) {
      this.buf = this.buf.slice(this.buf.length - OutputTail.MAX_CHARS);
    }
  }

  text(): string {
    return this.buf.trim();
  }
}

/** Human-readable reason a backend process died before it became healthy. */
function startupFailureMessage(
  code: number | null,
  signal: NodeJS.Signals | null,
  tail: string,
): string {
  const how = signal ? `was killed by ${signal}` : `exited with code ${code}`;
  const detail = tail ? `\n\n${tail}` : '';
  return (
    `The PixlStash backend ${how} during startup.${detail}` +
    `\n\nFull log: ${serverLogPath()}`
  );
}

/**
 * Issue a kill against the backend's whole process tree. Resolves with a human
 * message when the kill reported a real problem (so the caller can record it —
 * no silent failures), else null.
 *
 * On Windows the backend is spawned non-detached and Windows does NOT reap a
 * child when its parent dies (there is no kill-on-close job object), so an
 * un-awaited stop can leave the bundled python orphaned, holding
 * `resources\python\*.pyd/.dll` open — which is exactly what wedges a later
 * over-the-top update at the "Installing" step (issue #486). We therefore both
 * await the kill here and confirm the exit in `stop()`.
 */
function killTree(child: ChildProcess, detached: boolean): Promise<string | null> {
  const pid = child.pid;
  if (pid === undefined) return Promise.resolve(null);
  if (process.platform === 'win32') {
    return new Promise((resolve) => {
      execFile('taskkill', ['/pid', String(pid), '/T', '/F'], (err, _stdout, stderr) => {
        const detail = (stderr || err?.message || '').trim();
        // taskkill exits non-zero (128) when the process already exited — that's
        // success for us. Surface anything else so a stuck kill is diagnosable.
        if (err && !/not found|128|no (running )?tasks/i.test(detail)) {
          resolve(`taskkill for backend pid ${pid} failed: ${detail}`);
        } else {
          resolve(null);
        }
      });
    });
  }
  const signal = (sig: NodeJS.Signals): void => {
    try {
      // When we spawned the backend detached it leads its own process group, so
      // a negative pid takes down the whole tree (uvicorn + any workers). When
      // it shares our group (the dev supervisor case) we must target the single
      // pid — a negative pid would refer to a non-existent group and ESRCH.
      process.kill(detached ? -pid : pid, sig);
    } catch {
      try {
        child.kill(sig);
      } catch {
        /* already gone */
      }
    }
  };
  // Ask politely first so uvicorn can close the DB and sockets cleanly...
  signal('SIGTERM');
  // ...but a backend still importing torch can't service a Python-level SIGTERM
  // (its handler is deferred behind the long native call), so escalate to an
  // unmaskable SIGKILL shortly after. No-op once the process is already gone.
  setTimeout(() => signal('SIGKILL'), 1500).unref();
  return Promise.resolve(null);
}

/** Resolve once the child has actually exited, or after `timeoutMs` either way. */
function waitForExit(child: ChildProcess, timeoutMs: number): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, timeoutMs);
    child.once('exit', () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

/**
 * Owns the lifecycle of one PixlStash backend process: spawn it on a free
 * loopback port with a one-time desktop session token, wait until /version is
 * healthy, stream its logs to a file, and tear the whole process tree down on
 * stop/quit.
 */
export class ServerProcess {
  private child: ChildProcess | null = null;
  private logStream: WriteStream | null = null;
  private running: RunningServer | null = null;
  private exited = false;
  /** Set by stop() so the exit handler treats the death as intentional (e.g. a
   * settings-driven restart or app quit) and does NOT surface an error. */
  private stopping = false;
  /** Whether the backend was spawned in its own process group (see `start`). */
  private detached = false;

  constructor(private readonly onExit?: (code: number | null) => void) {}

  get info(): RunningServer | null {
    return this.running;
  }

  /**
   * Launch the server. In production we always run the bundled interpreter;
   * `overlayDir` (a GPU wheel overlay from BackendManager) is prepended to
   * PYTHONPATH so its torch/onnxruntime shadow the bundled CPU build. `null`
   * runs the bundled CPU/Metal env as-is.
   */
  async start(overlayDir: string | null, device?: string): Promise<RunningServer> {
    const python = isDevBackend() ? devInterpreter() : bundledInterpreter();
    if (!existsSync(python)) {
      throw new Error(`Backend interpreter not found: ${python}`);
    }

    const port = await findFreePort();
    const sessionToken = randomBytes(32).toString('hex');
    const url = `http://127.0.0.1:${port}`;

    await mkdir(dirname(serverLogPath()), { recursive: true });
    this.logStream = createWriteStream(serverLogPath(), { flags: 'a' });
    this.logStream.write(`\n=== ${new Date().toISOString()} starting ${python}${overlayDir ? ` (overlay ${overlayDir})` : ''} ===\n`);

    const pythonPath = overlayDir && !isDevBackend()
      ? [overlayDir, process.env.PYTHONPATH].filter(Boolean).join(delimiter)
      : process.env.PYTHONPATH;

    const env = {
      ...process.env,
      PYTHONPATH: pythonPath,
      PIXLSTASH_HOST: '127.0.0.1',
      PIXLSTASH_PORT: String(port),
      PIXLSTASH_INSTALL_TYPE: 'electron',
      PIXLSTASH_DESKTOP_SESSION: sessionToken,
      // Force the inference device to match this runtime (the bundled env is
      // CPU-only); overrides default_device in the shared on-disk config.
      ...(device ? { PIXLSTASH_DEFAULT_DEVICE: device } : {}),
    };

    // The desktop app always runs its OWN config (under the pixlstash-desktop
    // app-data dir), in both dev and packaged runs — never the standalone
    // server's config. A standalone pip/Docker server (launched without
    // --server-config) keeps using the plain `pixlstash` config, so the two
    // installs stay fully separate and never read or clobber each other.
    const args = ['-m', 'pixlstash.app', '--server-config', serverConfigPath()];

    // Normally we detach the backend into its own process group so we can take
    // down the whole tree with one signal. But under the dev supervisor
    // (scripts/dev-run.mjs) we deliberately DON'T: staying in the supervisor's
    // Electron process group lets it sweep the backend together with Electron's
    // own helpers on Ctrl-C. A terminal SIGINT never reaches a detached backend,
    // which is exactly how the dev server used to get orphaned.
    this.detached =
      process.platform !== 'win32' && process.env.PIXLSTASH_DESKTOP_SUPERVISED !== '1';

    this.child = spawn(python, args, {
      env,
      detached: this.detached,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    this.exited = false;
    this.child.stdout?.pipe(this.logStream, { end: false });
    this.child.stderr?.pipe(this.logStream, { end: false });

    // Keep the tail of the backend's output so a startup crash (e.g. an
    // incompatible server-config.json that fails a startup check) reports its
    // real cause rather than a bare timeout.
    const tail = new OutputTail();
    this.child.stdout?.on('data', (c) => tail.push(c));
    this.child.stderr?.on('data', (c) => tail.push(c));

    // If the process dies before it is healthy, reject startup immediately
    // instead of waiting out the health-check window.
    let ready = false;
    let failStartup: ((e: Error) => void) | null = null;
    const startupCrash = new Promise<never>((_, reject) => {
      failStartup = reject;
    });
    this.child.on('exit', (code, signal) => {
      this.exited = true;
      this.running = null;
      if (this.stopping) {
        // We asked it to stop (settings restart / quit) — death is expected.
        return;
      }
      if (ready) {
        // Crashed after a healthy start — let the owner surface it (dialog).
        this.onExit?.(code);
      } else {
        // Crashed during startup — fail fast with the captured reason.
        failStartup?.(new Error(startupFailureMessage(code, signal, tail.text())));
      }
    });

    try {
      // The first launch loads torch and runs startup checks; allow a generous
      // window. /version is auth-excluded so it answers as soon as uvicorn
      // binds. Race it against an early exit so a crashing backend reports at
      // once rather than after the full timeout.
      await Promise.race([pollHealth(`${url}/version`, 120_000), startupCrash]);
    } catch (e) {
      failStartup = null; // the kill below must not re-reject settled startup
      void this.stop(); // best-effort cleanup; don't delay surfacing the error
      throw e;
    }

    ready = true;
    this.running = { url, port, sessionToken };
    return this.running;
  }

  /**
   * Stop the backend and *await* confirmation its process tree is gone, so a
   * caller (app quit, settings restart, overlay move) can rely on the files
   * being released before it proceeds. Records the teardown into the server log
   * so a kill that misbehaves is diagnosable.
   */
  async stop(): Promise<void> {
    this.stopping = true;
    const child = this.child;
    const stream = this.logStream;
    this.child = null;
    this.running = null;
    this.logStream = null;
    try {
      if (child && !this.exited) {
        stream?.write(`\n=== ${new Date().toISOString()} stopping backend (pid ${child.pid}) ===\n`);
        const problem = await killTree(child, this.detached);
        if (problem) stream?.write(`${problem}\n`);
        await waitForExit(child, 5000);
        stream?.write(
          `=== backend ${this.exited ? 'stopped cleanly' : 'did NOT confirm exit within 5s'} ===\n`,
        );
      }
    } finally {
      stream?.end();
    }
  }
}
