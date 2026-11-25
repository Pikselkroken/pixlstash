// What the lightbox's stage box says while a clip the browser refuses gets a
// playable copy (#2807). The copy's status comes from
// GET/POST /pictures/video-copies/{id}; see api/pictures.js.

// The server's line: a copy estimated above this starts only when asked for,
// so a `preparing` copy above it is one somebody asked for.
const OFFER_ABOVE_SECONDS = 10;

function seconds(value) {
  const n = Math.max(1, Math.round(value));
  return `${n} second${n === 1 ? "" : "s"}`;
}

/** A clip's length: "6 s", "4 min", "4 min 12 s". */
export function clipLength(value) {
  const total = Math.max(0, Math.round(value));
  const min = Math.floor(total / 60);
  const sec = total % 60;
  if (!min) return `${sec} s`;
  return sec ? `${min} min ${sec} s` : `${min} min`;
}

/** An estimate: "3 seconds", "2 minutes". */
function roughly(value) {
  if (value < 60) return seconds(value);
  const n = Math.round(value / 60);
  return `${n} minute${n === 1 ? "" : "s"}`;
}

/** Time still to go: "3 seconds", "1 min 40 s". */
function left(value) {
  return value < 60 ? seconds(value) : clipLength(value);
}

/**
 * The stage box for a copy status, or null when the box is the plain "cannot
 * play" one (no copy asked for, or it failed, is unavailable, or is ready and
 * would not play either).
 *
 * @param {Object|null} status - the copy status; `{ state: "asking" }` while
 *   the first answer is awaited.
 * @returns {{wait: boolean, offer: boolean, head: string, text: string}|null}
 */
export function videoCopyNotice(status) {
  if (!status) return null;
  const format = status.format || "this format";
  if (status.state === "asking") {
    return { wait: true, offer: false, head: "", text: "" };
  }
  if (status.state === "preparing") {
    const togo = status.remaining_seconds ?? status.estimate_seconds;
    if (status.estimate_seconds > OFFER_ABOVE_SECONDS) {
      return {
        wait: true,
        offer: false,
        head: "Making a playable copy",
        text:
          (togo != null ? `About ${left(togo)} left. ` : "") +
          "It carries on if you move to another picture; the clip plays here when it is done.",
      };
    }
    return {
      wait: true,
      offer: false,
      head: "Preparing a playable copy",
      text:
        (togo != null ? `About ${seconds(togo)}. ` : "") +
        `This browser can't play ${format}, so PixlStash makes an MP4 copy once and keeps it.`,
    };
  }
  if (status.state === "offered") {
    const clip = status.duration_seconds
      ? `this ${clipLength(status.duration_seconds)} clip`
      : "this clip";
    const takes =
      status.estimate_seconds != null
        ? ` takes about ${roughly(status.estimate_seconds)} to make`
        : " takes a while to make";
    return {
      wait: false,
      offer: true,
      head: `This browser can't play ${format}`,
      text: `A playable copy of ${clip}${takes}. It is made once and kept.`,
    };
  }
  return null;
}
