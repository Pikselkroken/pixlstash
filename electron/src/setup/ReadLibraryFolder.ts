/**
 * Read the chosen library folder while the GPU runtime downloads.
 *
 * The read is the app's own `/folder-structure/read` - the same work the "Add a
 * library" wizard starts when it opens over a fresh library. Doing it here is
 * what turns two waits into one: the download is network, the read is disk, and
 * the setup screen is already on screen with a progress line and a tour to fill
 * it. By the time the window becomes the library, the folders are read and the
 * wizard opens on its questions instead of on a progress bar.
 *
 * Nothing here writes: the read proposes what each folder level looks like and
 * the owner still answers, in the app, before a single file is touched.
 */

export type ReadProgress = {
  processed: number;
  total: number;
  fraction: number;
  /**
   * `walking` counts folders, `faces` counts pictures, and a read with no
   * inference engine never reaches `faces` at all. The two are separate counts,
   * not two halves of one, so the screen names what it is counting rather than
   * pretending a single number ran from 0 to 100.
   */
  stage: string;
};

/** A fetch that carries the loopback session cookie (Electron's `net.fetch`). */
export type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

/**
 * Every route lives under this. The frontend's own client carries it as
 * `API_PREFIX` and the authz registry declares every policy against it
 * (`("POST", "/api/v1/folder-structure/read")`), so a call without it is a 404
 * - which is silent here by design, and therefore took a run on real hardware
 * to notice: the read never started, the line never got a count, and the app
 * read the folder again from scratch.
 */
const API_PREFIX = '/api/v1';

/** How often to ask a running read how far it has got. */
const POLL_MS = 700;

/**
 * A read of a big library takes minutes; one that has said nothing for this
 * long has not "not finished yet", it has stopped answering. Giving up here
 * costs the overlap and nothing else: the app starts its own read as it always
 * did.
 */
const SILENCE_LIMIT_MS = 10 * 60 * 1000;

function cookieHeader(sessionToken: string): Record<string, string> {
  return { cookie: `session_id=${sessionToken}`, 'content-type': 'application/json' };
}

/**
 * Start the read and follow it to the end.
 *
 * @returns the read's own RESULT when it completed, or null when it could not
 *   be started or did not finish. The result rather than the task id, because
 *   the task lives in the server's memory and the backend restarts onto the GPU
 *   runtime before the app ever loads: a parked task id resolved to "Task not
 *   found" every time. A null is not an error the user has to see - the app
 *   reads the folder itself, as it always did.
 */
export async function readLibraryFolder(
  fetcher: Fetcher,
  baseUrl: string,
  sessionToken: string,
  path: string,
  onProgress: (progress: ReadProgress & { failed?: boolean }) => void,
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
  now: () => number = Date.now,
): Promise<Record<string, unknown> | null> {
  let taskId: string;
  try {
    const response = await fetcher(`${baseUrl}${API_PREFIX}/folder-structure/read`, {
      method: 'POST',
      headers: cookieHeader(sessionToken),
      // `match_existing: false` for the same reason the wizard passes it: the
      // folder is read before its library is the active one, so matching names
      // against whatever IS active would name the wrong library's entities.
      body: JSON.stringify({ path, match_existing: false }),
    });
    if (!response.ok) {
      console.warn(`[startup] could not start the folder read: HTTP ${response.status}`);
      onProgress({ processed: 0, total: 0, fraction: -1, stage: '', failed: true });
      return null;
    }
    const started = (await response.json()) as { task_id?: string };
    if (!started?.task_id) {
      console.warn('[startup] the folder read started without a task id');
      onProgress({ processed: 0, total: 0, fraction: -1, stage: '', failed: true });
      return null;
    }
    taskId = started.task_id;
  } catch (e) {
    console.warn('[startup] could not start the folder read:', e);
    onProgress({ processed: 0, total: 0, fraction: -1, stage: '', failed: true });
    return null;
  }

  const deadline = now() + SILENCE_LIMIT_MS;
  for (;;) {
    if (now() > deadline) {
      console.warn('[startup] gave up waiting for the folder read; the app will read it itself');
      return null;
    }
    await sleep(POLL_MS);
    let status: {
      status?: string;
      stage?: string;
      processed?: number;
      total?: number;
      progress?: number;
      result?: Record<string, unknown> | null;
    };
    try {
      const response = await fetcher(
        `${baseUrl}${API_PREFIX}/folder-structure/read/status?task_id=${encodeURIComponent(taskId)}`,
        { headers: cookieHeader(sessionToken) },
      );
      if (!response.ok) {
        console.warn(`[startup] the folder read stopped answering: HTTP ${response.status}`);
        return null;
      }
      status = (await response.json()) as typeof status;
    } catch (e) {
      console.warn('[startup] the folder read stopped answering:', e);
      return null;
    }

    const processed = Number(status.processed) || 0;
    const total = Number(status.total) || 0;
    onProgress({
      processed,
      total,
      // The backend's own `progress` is a PERCENTAGE (`processed / total * 100`),
      // and reading it as a 0..1 fraction filled the bar at anything past one
      // per cent - a full bar at 50 of 153. The two counts it is derived from
      // are unambiguous, so the fraction comes from them.
      fraction: total > 0 ? Math.min(1, processed / total) : -1,
      stage: String(status.stage || ''),
    });

    // `cancelled` keeps whatever was found, so it is a usable result too - the
    // wizard shows the partial tree rather than starting over. A settled read
    // with no result is nothing to hand on.
    if (status.status === 'completed' || status.status === 'cancelled') {
      return status.result ?? null;
    }
    if (status.status === 'failed') {
      console.warn('[startup] the folder read failed; the app will read it itself');
      return null;
    }
  }
}
