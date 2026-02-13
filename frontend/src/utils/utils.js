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
  const time = `${hour}:${minute}`;
  switch (format) {
    case 'us':
      return `${month}/${day}/${year} ${time}`;
    case 'eu':
      return `${day}/${month}/${year} ${time}`;
    case 'ymd-slash':
      return `${year}/${month}/${day} ${time}`;
    case 'ymd-dot':
      return `${year}.${month}.${day} ${time}`;
    case 'ymd-jp':
      return `${year}年${month}月${day}日 ${time}`;
    case 'locale':
      return d.toLocaleString();
    case 'iso':
    default:
      return `${year}-${month}-${day} ${time}`;
  }
}

export function formatIsoDate(dateStr) {
  return formatUserDate(dateStr, 'iso');
}

export function StackThreshold(value) {
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

export function handBoxColor(idx) {
  const palette = [
    '#00e5ff',  // cyan
    '#ff6d00',  // orange
    '#00e676',  // green
    '#f50057',  // pink
    '#651fff',  // purple
    '#c0ca33',  // lime
    '#ff1744',  // red
    '#18ffff',  // teal
  ];
  return palette[idx % palette.length];
}