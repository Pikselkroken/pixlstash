function normalizedExtension(value) {
  const extension = String(value || "")
    .replace(/^\./, "")
    .toLowerCase();
  return /^[a-z0-9]{1,16}$/.test(extension) ? extension : "";
}

export function overlaySaveAsStem(suggestedName, originalExtension) {
  const name = typeof suggestedName === "string" ? suggestedName.trim() : "";
  const extension = normalizedExtension(originalExtension);
  const suffix = extension ? `.${extension}` : "";
  return suffix && name.toLowerCase().endsWith(suffix)
    ? name.slice(0, -suffix.length)
    : name;
}

export function normalizeOverlaySaveAsFilename(value, originalExtension) {
  const stem = typeof value === "string" ? value.trim() : "";
  if (!stem) return { filename: "", error: "Enter a filename." };
  // Use the strictest supported desktop filesystem rules so a name chosen in
  // Firefox behaves the same if PixlStash is later opened on Windows.
  // eslint-disable-next-line no-control-regex
  if (/[/\\\u0000-\u001f\u007f<>:"|?*]/.test(stem) || /[. ]$/.test(stem)) {
    return {
      filename: "",
      error: "Use a filename without slashes or reserved characters.",
    };
  }
  const extension = normalizedExtension(originalExtension);
  return {
    filename: extension ? `${stem}.${extension}` : stem,
    error: "",
  };
}
