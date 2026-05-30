// Shared helpers for snapshot UI bits (chip color, relative-date formatting).
// Live separately from utils.js to keep snapshot-only logic out of the
// general-purpose helpers and to centralise the "treat naive ISO as UTC"
// quirk so a regression in one component doesn't get fixed in only one place.

export function kindChipColor(kind) {
  const map = {
    MANUAL: "primary",
    DAILY: "secondary",
    WEEKLY: "info",
    MONTHLY: "success",
    OPPORTUNISTIC: "warning",
  };
  return map[kind] ?? "default";
}

// Backend may emit either a naive ISO string (treat as UTC, append "Z") or
// one that already carries "Z" / a numeric offset. Appending "Z" to the
// latter yields ".+00:00Z" which Date parses as Invalid Date.
export function relativeDate(isoStr) {
  if (!isoStr) return "";
  const normalized =
    isoStr.includes("T") &&
    !isoStr.endsWith("Z") &&
    !/[+-]\d{2}:\d{2}$/.test(isoStr)
      ? isoStr + "Z"
      : isoStr;
  const diff = (Date.now() - new Date(normalized).getTime()) / 1000;
  if (Number.isNaN(diff)) return "";
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
