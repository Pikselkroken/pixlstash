export function toggleScore(currentScore, targetScore) {
  const current = Number(currentScore || 0);
  const target = Number(targetScore || 0);
  if (!Number.isFinite(target)) return current;
  return current === target ? 0 : target;
}

function formatDateParts(date) {
  const pad = (n) => String(n).padStart(2, '0');
  return {
    year: String(date.getFullYear()),
    month: pad(date.getMonth() + 1),
    day: pad(date.getDate()),
    hour: pad(date.getHours()),
    minute: pad(date.getMinutes()),
  };
}

export function formatUserDate(dateStr, format) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  const {year, month, day, hour, minute} = formatDateParts(d);
  // Helper for AM/PM time
  function ampmTime(date) {
    let h = date.getHours();
    const m = date.getMinutes();
    const ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12;
    if (h === 0) h = 12;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')} ${
        ampm}`;
  }
  const time24 = `${hour}:${minute}`;
  switch (format) {
    case 'us':
      return `${month}/${day}/${year} ${ampmTime(d)}`;
    case 'british':
      return `${day}/${month}/${year} ${ampmTime(d)}`;
    case 'eu':
      return `${day}/${month}/${year} ${time24}`;
    case 'ymd-slash':
      return `${year}/${month}/${day} ${time24}`;
    case 'ymd-dot':
      return `${year}.${month}.${day} ${time24}`;
    case 'ymd-jp':
      return `${year}年${month}月${day}日 ${time24}`;
    case 'locale':
      // Use toLocaleString with options to avoid seconds
      return d.toLocaleString(undefined, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      });
    case 'iso':
    default:
      return `${year}-${month}-${day} ${time24}`;
  }
}

export function formatIsoDate(dateStr) {
  return formatUserDate(dateStr, 'iso');
}

export function getStackThreshold(value) {
  if (value === null || value === undefined || value === '') return 0.9;
  const parsed = parseFloat(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return 0.9;
  return Math.max(0.5, Math.min(0.99999, parsed));
}

export function getStackColor(stackIndex, step = 47) {
  const hue = (stackIndex * step) % 360;
  return `hsl(${hue} 70% 55%)`;
}

// Add this helper below your script setup imports
export function faceBoxColor(idx) {
  // Pick from a palette, cycle if more faces than colors
  const palette = [
    '#ff5252',  // red
    '#40c4ff',  // blue
    '#ffd740',  // yellow
    '#69f0ae',  // green
    '#d500f9',  // purple
    '#ffab40',  // orange
    '#00e676',  // teal
    '#ff4081',  // pink
    '#8d6e63',  // brown
    '#7c4dff',  // indigo
  ];
  return palette[idx % palette.length];
}

export function getInfoFont(el) {
  if (typeof window === 'undefined' || !el) return null;
  const style = window.getComputedStyle(el);
  return `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
}

export function applyStackBackgroundAlpha(color) {
  if (!color || typeof color !== 'string') return color;
  const trimmed = color.trim();
  if (!trimmed) return color;
  if (trimmed.startsWith('hsla(') || trimmed.startsWith('rgba(')) {
    return trimmed;
  }
  if (trimmed.startsWith('hsl(')) {
    const inner = trimmed.slice(4, -1).trim();
    if (inner.includes(',')) {
      return `hsla(${inner}, 0.6)`;
    }
    return `hsl(${inner} / 0.6)`;
  }
  if (trimmed.startsWith('rgb(')) {
    const inner = trimmed.slice(4, -1).trim();
    if (inner.includes(',')) {
      return `rgba(${inner}, 0.6)`;
    }
    return `rgb(${inner} / 0.6)`;
  }
  return trimmed;
}

export function getStackColorIndexFromId(stackId) {
  if (stackId === null || stackId === undefined) return null;
  const numeric = Number(stackId);
  if (Number.isFinite(numeric)) return numeric;
  const raw = String(stackId);
  let hash = 0;
  for (let i = 0; i < raw.length; i += 1) {
    hash = (hash * 31 + raw.charCodeAt(i)) % 2147483647;
  }
  return hash || null;
}

export function normalizePluginProgressMessage(message, fallback) {
  const raw = String(message || '').trim() || String(fallback || '').trim();
  if (!raw) return '';

  let text = raw;

  for (let i = 0; i < 3; i += 1) {
    const trimmed = text.trim();
    if (!(trimmed.startsWith('"') && trimmed.endsWith('"'))) {
      break;
    }
    try {
      const parsed = JSON.parse(trimmed);
      if (typeof parsed !== 'string') {
        break;
      }
      text = parsed;
    } catch {
      break;
    }
  }

  for (let i = 0; i < 5; i += 1) {
    const next = text.replace(/\\+r\\+n/g, '\n')
                     .replace(/\\+n/g, '\n')
                     .replace(/\\+\n/g, '\n');
    if (next === text) {
      break;
    }
    text = next;
  }

  return text;
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function arraysEqualByString(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b)) return false;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (String(a[i]) !== String(b[i])) return false;
  }
  return true;
}

export function isRangeOverlap(startA, endA, startB, endB) {
  return Math.max(startA, startB) < Math.min(endA, endB);
}

export function rangeCovers(ranges, start, end) {
  return ranges.some(
      ([rangeStart, rangeEnd]) => start >= rangeStart && end <= rangeEnd,
  );
}

export function shiftRangesForDelta(ranges, start, delta, end = null) {
  if (!Array.isArray(ranges) || !ranges.length || delta === 0) return ranges;
  const result = [];
  const useEnd = typeof end === 'number';
  for (const [rangeStart, rangeEnd] of ranges) {
    if (useEnd) {
      if (rangeEnd <= start) {
        result.push([rangeStart, rangeEnd]);
        continue;
      }
      if (rangeStart >= end) {
        result.push([rangeStart + delta, rangeEnd + delta]);
        continue;
      }
      continue;
    }
    if (rangeEnd <= start) {
      result.push([rangeStart, rangeEnd]);
      continue;
    }
    if (rangeStart >= start) {
      result.push([rangeStart + delta, rangeEnd + delta]);
      continue;
    }
  }
  return result;
}
