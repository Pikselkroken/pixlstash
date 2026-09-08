/**
 * What "Get started" actually does, as one function with its collaborators
 * passed in.
 *
 * This lives outside `main.ts` because the interesting part is the ORDER, and
 * the order has more outcomes than anyone can hold in their head: the download
 * has to finish before the backend starts, or the read it feeds runs on the CPU
 * runtime; the read can fail, stall, or return nothing; the identity import can
 * refuse; the backend can demand a permission repair the user may decline; and
 * a machine with no GPU skips half of it. Every one of those is a real first
 * run for somebody, and none of them can be exercised through an IPC handler
 * that needs Electron, a window and a real 2.5 GB download.
 */

export type SetupChoices = {
  imageRoot: string;
  useGpu: boolean;
  installLocation?: string;
  importLegacyIdentity?: boolean;
  telemetry?: Record<string, boolean> | null;
};

export type SetupDeps<Accel> = {
  /** The accelerator this machine could install, or null when there is none. */
  gpu: Accel | null;
  /** Where a detected standalone library lives, for the identity import. */
  legacyIdentitySource: string | null;
  /** Resolve a path the way the host does, for comparing against the above. */
  resolvePath: (path: string) => string;
  /** Remember where the GPU runtime should install, before it downloads. */
  setBackendsRoot: (location: string) => void;
  /** Prepare the one-time identity migration. Throws to refuse the setup. */
  prepareLegacyIdentity: (source: string) => Promise<unknown>;
  /** Write the desktop's own server config. */
  writeConfig: (imageRoot: string) => void;
  /**
   * Remove the config this run wrote, for a setup that then could not finish.
   *
   * The config outlives the process, and launch shows the wizard only when
   * there ISN'T one. A config left behind by a setup that failed therefore
   * removes the folder picker from every later launch, and the folder it names
   * is exactly the one the failure was about - declining the recreate offer
   * trapped the choice it was asking you to change.
   */
  clearConfig: () => void;
  /** Park the privacy answer for the app, or clear a stale one with null. */
  parkTelemetry: (patch: Record<string, boolean> | null) => void;
  /** Park a finished folder read for the app, or clear one with null. */
  parkMapping: (entry: { path: string; result: Record<string, unknown> } | null) => void;
  setActiveAccel: (accel: Accel | null) => Promise<unknown>;
  /** The overlay already installed and usable, if any. */
  activeOverlayAccel: () => Promise<Accel | null>;
  /**
   * Start the backend. `navigate: false` leaves the window on the setup screen,
   * which is what lets the read and the download share the wait.
   */
  startBackend: (accel: Accel | null, navigate: boolean) => Promise<void>;
  installOverlay: (accel: Accel) => Promise<unknown>;
  /** Read the library folder through the running server. Null when it could not. */
  readFolder: (imageRoot: string) => Promise<Record<string, unknown> | null>;
  /** Tell the setup screen the reading has begun. */
  announceReading: () => void;
  /**
   * Point the window at the running backend: the end of setup.
   *
   * Separate from `startBackend` because the read has to finish first. The
   * mapping it parks is collected by the app as it loads, so a window handed
   * over before the read ends is a window that opens on a progress bar.
   */
  navigateToApp: () => Promise<void>;
};

/**
 * Run a first-run setup to the point where the window becomes the library.
 *
 * Throws when setup cannot proceed (no library folder, a refused identity
 * import, a backend that will not start). The renderer keeps the install step
 * on screen and shows the message.
 */
export async function runFirstRunSetup<Accel>(
  choices: SetupChoices,
  deps: SetupDeps<Accel>,
): Promise<void> {
  const imageRoot = (choices?.imageRoot || '').trim();
  if (!imageRoot) throw new Error('Please choose a library folder.');

  // Where the GPU runtime installs, recorded before anything downloads into it.
  const installLocation = (choices?.installLocation || '').trim();
  if (installLocation) deps.setBackendsRoot(installLocation);

  if (choices?.importLegacyIdentity) {
    if (!deps.legacyIdentitySource) {
      throw new Error('No existing standalone PixlStash library was detected.');
    }
    if (deps.resolvePath(imageRoot) !== deps.legacyIdentitySource) {
      throw new Error(
        'To import its login and share links, keep the detected existing library selected.',
      );
    }
    // Fail closed before a live desktop config exists: a nonzero exit leaves the
    // vault untouched and keeps the setup screen open, rather than booting a
    // server that looks migrated and is not.
    await deps.prepareLegacyIdentity(deps.legacyIdentitySource);
  }

  // The config is written only once any requested preparation has succeeded.
  deps.writeConfig(imageRoot);

  try {
    await afterConfig(imageRoot, choices, deps);
  } catch (error) {
    // Everything above this point refuses BEFORE writing a config, and every
    // refusal below has already written one. A config is what tells the next
    // launch there is nothing to ask, so leaving one behind for a setup that
    // never finished takes away the folder picker - and the folder it names is
    // the one the person was being asked to change.
    try {
      deps.clearConfig();
    } catch {}
    try {
      deps.parkTelemetry(null);
    } catch {}
    throw error;
  }
}

/** Everything after the config is written, and everything it is rolled back for. */
async function afterConfig<Accel>(
  imageRoot: string,
  choices: SetupChoices,
  deps: SetupDeps<Accel>,
): Promise<void> {
  // The privacy answer belongs to an owner record that does not exist yet, so
  // it waits here for the app. After the config, so a setup that failed earlier
  // leaves no answer to a question the user may be asked again.
  deps.parkTelemetry(choices?.telemetry ?? null);

  const gpu = deps.gpu;
  if (!choices?.useGpu || !gpu) {
    // Nothing to download, so nothing to wait for: start, and go.
    await deps.setActiveAccel(null);
    await deps.startBackend(await deps.activeOverlayAccel(), true);
    return;
  }

  // The download and the read used to run AT THE SAME TIME, the read on the
  // bundled CPU runtime, and on most machines that was a loss. Nothing can move
  // a running backend onto the GPU - the device is fixed when the process is
  // spawned and InsightFace picks its providers on first use - so overlapping
  // meant the whole face pass ran on CPU. Waiting is quicker whenever the CPU
  // read is more than one download slower than the GPU read, which on a real
  // library it is by minutes: the overlap saved the download and paid for it
  // several times over.
  //
  // So: download, start once, on the GPU, and read on it. The read still ends
  // before the window becomes the library, which is what keeps the wizard
  // opening on its questions instead of on a progress bar.
  await deps.installOverlay(gpu);
  await deps.setActiveAccel(gpu);
  await deps.startBackend(gpu, false);

  deps.announceReading();
  deps.parkMapping(null);
  try {
    const result = await deps.readFolder(imageRoot);
    if (result) deps.parkMapping({ path: imageRoot, result });
  } catch (e) {
    // A read that throws costs the wizard its head start and nothing else: the
    // app reads the folder itself, exactly as it did before any of this existed.
    console.warn('[startup] the folder read failed:', e);
  }

  await deps.navigateToApp();
}
