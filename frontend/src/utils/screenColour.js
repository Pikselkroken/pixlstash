/**
 * Pick a colour off the screen, for search by colour (#2151).
 *
 * The desktop app cannot use the browser's EyeDropper: Electron exposes it,
 * but its `open()` rejects at once as if cancelled. So there the shell samples
 * its own window (`pixlstashDesktop.samplePixel`): the next click anywhere in
 * the window is the sample. A browser uses Chromium's EyeDropper, which
 * reaches the whole screen; one without it offers no picker.
 */

const PICK_CLASS = "colour-picking";

function desktopSampler() {
  return typeof window !== "undefined"
    ? window.pixlstashDesktop?.samplePixel
    : undefined;
}

export function canPickScreenColour() {
  return (
    typeof window !== "undefined" &&
    (Boolean(desktopSampler()) || "EyeDropper" in window)
  );
}

/** The picked "#rrggbb", or null when the person cancels (Esc, right click). */
export async function pickScreenColour() {
  const sample = desktopSampler();
  if (sample) return pickInWindow(sample);
  try {
    return (await new window.EyeDropper().open()).sRGBHex;
  } catch (err) {
    if (err?.name !== "AbortError") {
      console.warn("[screenColour] screen colour pick failed:", err);
    }
    return null;
  }
}

/**
 * The next click in the window is the sample. Until then the whole page
 * ignores the pointer (`.colour-picking` in App.css), so hover tints stay off
 * the pixel and the click opens nothing, and every press is swallowed here
 * first, keys included, so an open list or menu stays open.
 */
function pickInWindow(samplePixel) {
  return new Promise((resolve) => {
    let sampled = Promise.resolve(null);
    const swallow = (e) => {
      e.preventDefault();
      e.stopImmediatePropagation();
    };
    const finish = (result) => {
      document.documentElement.classList.remove(PICK_CLASS);
      for (const [type, fn] of listeners) {
        window.removeEventListener(type, fn, true);
      }
      resolve(result);
    };
    const listeners = [
      [
        "pointerdown",
        (e) => {
          swallow(e);
          if (e.button !== 0) return;
          sampled = Promise.resolve(samplePixel(e.clientX, e.clientY)).catch(
            (err) => {
              console.warn("[screenColour] sampling the window failed:", err);
              return null;
            },
          );
        },
      ],
      ["mousedown", swallow],
      ["pointerup", swallow],
      ["mouseup", swallow],
      ["click", (e) => (swallow(e), finish(sampled))],
      ["contextmenu", (e) => (swallow(e), finish(null))],
      [
        "keydown",
        // Every key too: Space or Enter on the still-focused eyedropper
        // would otherwise start a second pick under this one.
        (e) => {
          swallow(e);
          if (e.key === "Escape") finish(null);
        },
      ],
    ];
    for (const [type, fn] of listeners) {
      window.addEventListener(type, fn, true);
    }
    document.documentElement.classList.add(PICK_CLASS);
  });
}
