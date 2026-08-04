export function normalizeOverlaySaveAsFilename(value, originalExtension) {
  const name = typeof value === "string" ? value.trim() : "";
  if (!name) return { filename: "", error: "Enter a filename." };
  // Use the strictest supported desktop filesystem rules so a name chosen in
  // Firefox behaves the same if PixlStash is later opened on Windows.
  // eslint-disable-next-line no-control-regex
  if (/[/\\\u0000-\u001f\u007f<>:"|?*]/.test(name) || /[. ]$/.test(name)) {
    return {
      filename: "",
      error: "Use a filename without slashes or reserved characters.",
    };
  }
  const extension = String(originalExtension || "")
    .replace(/^\./, "")
    .toLowerCase();
  const hasExtension = name.lastIndexOf(".") > 0;
  return {
    filename: !hasExtension && extension ? `${name}.${extension}` : name,
    error: "",
  };
}
