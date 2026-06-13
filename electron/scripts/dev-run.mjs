// Dev supervisor for `npm run dev` / `npm run start`.
//
// Why this exists: a terminal Ctrl-C delivers SIGINT to the whole foreground
// process group, but neither half of Electron cleans up from it —
//   - Electron's MAIN process does not run `app.on('before-quit')` on a bare
//     SIGINT (Chromium just terminates it), so its teardown never fires; and
//   - Chromium's HELPER processes (GPU, network service, broker) ignore SIGINT.
// On top of that the Python backend is spawned in its own session, so a terminal
// SIGINT never reaches it at all. The result: Ctrl-C leaves an orphaned backend
// plus orphaned GPU/network/broker processes. (Verified by reproducing it: a
// group SIGINT against the old setup left python + gpu + network + broker alive.)
//
// A plain Node process, unlike Electron, DOES get its signal handlers invoked.
// So this script becomes the single owner of teardown: it launches Electron as
// the leader of its OWN process group and, on any termination, signals that
// whole group at once — Electron main, every helper, and (because the backend
// runs un-detached under us; see ServerProcess) the Python server too.
import electronPath from 'electron';
import { spawn } from 'node:child_process';

const child = spawn(electronPath, ['.', ...process.argv.slice(2)], {
  stdio: 'inherit',
  // New session/process group: the terminal's Ctrl-C no longer hits Electron
  // directly — this supervisor owns its lifecycle and tears the group down.
  detached: true,
  // Tell ServerProcess to keep the backend in our group so it gets swept too.
  env: { ...process.env, PIXLSTASH_DESKTOP_SUPERVISED: '1' },
});

let tearingDown = false;
let backstop = null;

/** Signal the whole process group led by Electron main (negative pid). */
function signalGroup(signal) {
  if (child.pid === undefined) return;
  try {
    process.kill(-child.pid, signal);
  } catch {
    /* group already gone */
  }
}

function teardown() {
  if (tearingDown) return;
  tearingDown = true;
  // Ask the Electron MAIN process to quit (not the whole group): it tears down
  // its own helpers (GPU, network service, renderer) in order and runs
  // before-quit to stop the backend. Killing the helpers out from under a
  // still-running main instead makes Chromium panic and spam "GPU process
  // isn't usable / Network service crashed" as it tries to restart them.
  try {
    child.kill('SIGTERM');
  } catch {
    /* already gone */
  }
  // Backstop only: if the graceful quit stalls, SIGKILL the whole group
  // (Electron + helpers + the in-group backend) so Ctrl-C never hangs or leaks.
  backstop = setTimeout(() => {
    signalGroup('SIGKILL');
    process.exit(0);
  }, 4000);
}

for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
  process.on(sig, teardown);
}

// Electron exited (graceful quit, window close, Cmd-Q, or a crash). A clean quit
// already stopped the backend via before-quit; a crash may not have — so sweep
// the group once to catch any straggler (no-op if already empty), then follow
// it out and cancel the backstop.
child.on('exit', (code, signal) => {
  if (backstop) clearTimeout(backstop);
  signalGroup('SIGKILL');
  process.exit(tearingDown ? 0 : code ?? (signal ? 1 : 0));
});

child.on('error', (err) => {
  console.error('dev-run: failed to launch Electron:', err);
  process.exit(1);
});
