/** Owner-safe window title. A read-only/share session never receives a name. */
export function libraryDocumentTitle(activeLibraryName, readOnly = false) {
  const name = readOnly ? "" : String(activeLibraryName ?? "").trim();
  return name ? `PixlStash - ${name}` : "PixlStash";
}
