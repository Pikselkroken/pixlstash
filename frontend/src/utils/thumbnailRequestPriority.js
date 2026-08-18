function finiteInteger(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(0, Math.floor(numeric)) : fallback;
}

/**
 * Keep the real viewport eager while allowing the virtualizer's buffer to load
 * lazily. Only the first visible row receives high network priority.
 */
export function thumbnailRequestWindow({
  visibleStart,
  visibleEnd,
  renderStart,
  renderEnd,
  columns,
  rowStarts,
}) {
  const bufferedStart = finiteInteger(renderStart);
  const bufferedEnd = Math.max(bufferedStart, finiteInteger(renderEnd));
  const viewportStart = finiteInteger(visibleStart);
  const viewportEnd = Math.max(viewportStart, finiteInteger(visibleEnd));
  const hasVisibleWindow = viewportEnd > viewportStart;
  const eagerStart = hasVisibleWindow ? viewportStart : bufferedStart;
  const eagerEnd = hasVisibleWindow ? viewportEnd : bufferedEnd;
  const columnCount = Math.max(1, finiteInteger(columns, 1));

  let highEnd = Math.min(eagerEnd, eagerStart + columnCount);
  if (Array.isArray(rowStarts)) {
    const nextRowStart = rowStarts.find(
      (rowStart) => finiteInteger(rowStart) > eagerStart,
    );
    if (nextRowStart !== undefined) {
      highEnd = Math.min(eagerEnd, finiteInteger(nextRowStart));
    }
  }

  if (eagerEnd > eagerStart) highEnd = Math.max(eagerStart + 1, highEnd);

  return {
    eagerStart,
    eagerEnd,
    highStart: eagerStart,
    highEnd,
  };
}

export function loadingModeForThumbnail(index, bounds) {
  const normalizedIndex = Number(index);
  return Number.isFinite(normalizedIndex) &&
    normalizedIndex >= bounds.eagerStart &&
    normalizedIndex < bounds.eagerEnd
    ? "eager"
    : "lazy";
}

export function fetchPriorityForThumbnail(index, bounds) {
  const normalizedIndex = Number(index);
  return Number.isFinite(normalizedIndex) &&
    normalizedIndex >= bounds.highStart &&
    normalizedIndex < bounds.highEnd
    ? "high"
    : "auto";
}
