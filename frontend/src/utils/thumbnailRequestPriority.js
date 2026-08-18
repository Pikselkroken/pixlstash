function finiteInteger(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(0, Math.floor(numeric)) : fallback;
}

/**
 * Eagerly load the virtualizer's rendered buffer so it remains a real prefetch
 * runway during fast scrolling. The visible window receives high network
 * priority; buffered thumbnails stay at normal priority so they cannot jump
 * ahead of what the user is currently looking at.
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
  const eagerStart = bufferedStart;
  const eagerEnd = bufferedEnd;
  const columnCount = Math.max(1, finiteInteger(columns, 1));

  const highStart = hasVisibleWindow
    ? Math.min(bufferedEnd, Math.max(bufferedStart, viewportStart))
    : bufferedStart;
  let highEnd = hasVisibleWindow
    ? Math.max(highStart, Math.min(bufferedEnd, viewportEnd))
    : Math.min(bufferedEnd, bufferedStart + columnCount);
  if (!hasVisibleWindow && Array.isArray(rowStarts)) {
    const nextRowStart = rowStarts.find(
      (rowStart) => finiteInteger(rowStart) > bufferedStart,
    );
    if (nextRowStart !== undefined) {
      highEnd = Math.min(bufferedEnd, finiteInteger(nextRowStart));
    }
  }

  if (bufferedEnd > highStart) {
    highEnd = Math.min(bufferedEnd, Math.max(highStart + 1, highEnd));
  }

  return {
    eagerStart,
    eagerEnd,
    highStart,
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
