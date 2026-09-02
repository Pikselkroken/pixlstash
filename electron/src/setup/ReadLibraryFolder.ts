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

export type ReadProgress = { processed: number; total: number; fraction: number };

/** A fetch that carries the loopback session cookie (Electron's `net.fetch`). */
export type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

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
 * @returns the task id when the read completed, or null when it could not be
 *   started or did not finish. A null is not an error the user has to see: the
 *   app falls back to reading the folder itself.
 */
export async function readLibraryFolder(
  fetcher: Fetcher,
  baseUrl: string,
  sessionToken: string,
  path: string,
  onProgress: (progress: ReadProgress) => void,
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
  now: () => number = Date.now,
): Promise<string | null> {
  let taskId: string;
  try {
    const response = await fetcher(`${baseUrl}/folder-structure/read`, {
      method: 'POST',
      headers: cookieHeader(sessionToken),
      // `match_existing: false` for the same reason the wizard passes it: the
      // folder is read before its library is the active one, so matching names
      // against whatever IS active would name the wrong library's entities.
      body: JSON.stringify({ path, match_existing: false }),
    });
    if (!response.ok) {
      console.warn(`[startup] could not start the folder read: HTTP ${response.status}`);
      return null;
    }
    const started = (await response.json()) as { task_id?: string };
    if (!started?.task_id) {
      console.warn('[startup] the folder read started without a task id');
      return null;
    }
    taskId = started.task_id;
  } catch (e) {
    console.warn('[startup] could not start the folder read:', e);
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
      processed?: number;
      total?: number;
      progress?: number;
    };
    try {
      const response = await fetcher(
        `${baseUrl}/folder-structure/read/status?task_id=${encodeURIComponent(taskId)}`,
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

    onProgress({
      processed: Number(status.processed) || 0,
      total: Number(status.total) || 0,
      fraction: typeof status.progress === 'number' ? status.progress : -1,
    });

    // `cancelled` keeps whatever was found, so it is a usable result too - the
    // wizard shows the partial tree rather than starting over.
    if (status.status === 'completed' || status.status === 'cancelled') return taskId;
    if (status.status === 'failed') {
      console.warn('[startup] the folder read failed; the app will read it itself');
      return null;
    }
  }
}
