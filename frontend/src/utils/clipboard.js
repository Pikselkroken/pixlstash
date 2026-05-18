/**
 * Copies text to the clipboard with a fallback for non-secure contexts
 * (e.g. HTTP on Firefox/Linux where navigator.clipboard is unavailable).
 *
 * Returns true if the copy succeeded, false otherwise.
 */
export async function copyText(text) {
  if (!text) return false;

  // Normalize line endings to \r\n on Windows to avoid Firefox clipboard bugs
  if (
    typeof navigator !== "undefined" &&
    navigator.userAgent?.includes("Windows")
  ) {
    text = text.replace(/\r?\n/g, "\r\n");
  }

  // Try the modern Clipboard API first (requires secure context or localhost)
  if (navigator?.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to execCommand fallback
    }
  }

  // Fallback: intercept the browser's copy event
  try {
    let intercepted = false;
    const handler = (e) => {
      e.clipboardData.setData("text/plain", text);
      e.preventDefault();
      intercepted = true;
    };
    document.addEventListener("copy", handler);
    document.execCommand("copy");
    document.removeEventListener("copy", handler);
    return intercepted;
  } catch {
    return false;
  }
}
