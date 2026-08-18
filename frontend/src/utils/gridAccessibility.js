/** A stable, concise name for a picture tile when no authored caption exists. */
export function pictureGridLabel(image, { video = false } = {}) {
  const path = String(image?.file_path ?? "").trim();
  const filename = path ? path.split(/[\\/]/).filter(Boolean).pop() : "";
  const ordinal = Number.isFinite(Number(image?.idx))
    ? Number(image.idx) + 1
    : null;
  const id = image?.id != null ? String(image.id) : "";
  const identity = filename || (ordinal ? `item ${ordinal}` : id ? `ID ${id}` : "item");
  const parts = [`${video ? "Video" : "Picture"} ${identity}`];

  const width = Number(image?.width);
  const height = Number(image?.height);
  if (width > 0 && height > 0) parts.push(`${width} by ${height} pixels`);

  return parts.join(", ");
}

/**
 * Roving-tabindex rule for a virtualized grid: one operable rendered tile is in
 * the document tab order, while placeholders and undo ghosts remain inert.
 */
export function pictureGridTabIndex(
  image,
  { cursorIndex = null, fallbackIndex = -1, ghosted = false } = {},
) {
  if (!image?.id || ghosted) return -1;
  const activeIndex = cursorIndex == null ? fallbackIndex : cursorIndex;
  return Number(image.idx) === Number(activeIndex) ? 0 : -1;
}
