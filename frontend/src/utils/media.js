// media.js - Shared media/file helpers for PixlStash frontend

export const PIL_IMAGE_EXTENSIONS = [
  "jpg",
  "jpeg",
  "png",
  "bmp",
  "gif",
  "tiff",
  "tif",
  "webp",
  "ppm",
  "pgm",
  "pbm",
  "pnm",
  "ico",
  "icns",
  "svg",
  "dds",
  "msp",
  "pcx",
  "xbm",
  "im",
  "fli",
  "flc",
  "eps",
  "psd",
  "pdf",
  "jp2",
  "j2k",
  "jpf",
  "jpx",
  "j2c",
  "jpc",
  "tga",
  "ras",
  "sgi",
  "rgb",
  "rgba",
  "bw",
  "exr",
  "hdr",
  "pic",
  "pict",
  "pct",
  "cur",
  "emf",
  "wmf",
  "heic",
  "heif",
  "avif",
];

export const VIDEO_EXTENSIONS = [
  "mp4",
  "avi",
  "mov",
  "webm",
  "mkv",
  "flv",
  "wmv",
  "m4v",
];

const ARCHIVE_EXTENSIONS = ["zip"];

const CAPTION_EXTENSIONS = ["txt"];

export function isSupportedImageFile(file) {
  const filename = typeof file === "string" ? file : file?.name || "";
  const ext = filename.split(".").pop().toLowerCase();
  return PIL_IMAGE_EXTENSIONS.includes(ext);
}

export function isSupportedVideoFile(file) {
  const filename = typeof file === "string" ? file : file.name || "";

  const ext = filename.split(".").pop().toLowerCase();
  return VIDEO_EXTENSIONS.includes(ext);
}

function isSupportedArchiveFile(file) {
  const filename = typeof file === "string" ? file : file.name || "";
  const ext = filename.split(".").pop().toLowerCase();
  return ARCHIVE_EXTENSIONS.includes(ext);
}

function isSupportedMediaFile(file) {
  return isSupportedImageFile(file) || isSupportedVideoFile(file);
}

function isSupportedCaptionFile(file) {
  const filename = typeof file === "string" ? file : file?.name || "";
  const lastDot = filename.lastIndexOf(".");
  const ext =
    lastDot > 0 && lastDot < filename.length - 1
      ? filename.slice(lastDot + 1).toLowerCase()
      : "";
  return CAPTION_EXTENSIONS.includes(ext);
}

export function isSupportedImportFile(file) {
  return (
    isSupportedMediaFile(file) ||
    isSupportedArchiveFile(file) ||
    isSupportedCaptionFile(file)
  );
}

function _fileDedupKey(file) {
  const name = file?.name || "";
  const size = Number.isFinite(file?.size) ? file.size : 0;
  const lastModified = Number.isFinite(file?.lastModified)
    ? file.lastModified
    : 0;
  return `${name}::${size}::${lastModified}`;
}

function _addIfSupportedFile(file, uniqueMap) {
  if (!file || !isSupportedImportFile(file)) return;
  const key = _fileDedupKey(file);
  if (!uniqueMap.has(key)) {
    uniqueMap.set(key, file);
  }
}

function _readAllWebkitDirectoryEntries(reader) {
  return new Promise((resolve) => {
    const entries = [];
    const readBatch = () => {
      reader.readEntries((batch) => {
        if (!batch || batch.length === 0) {
          resolve(entries);
          return;
        }
        entries.push(...batch);
        readBatch();
      });
    };
    readBatch();
  });
}

async function _collectFromWebkitEntry(entry, uniqueMap) {
  if (!entry) return;
  if (entry.isFile) {
    await new Promise((resolve) => {
      entry.file(
        (file) => {
          _addIfSupportedFile(file, uniqueMap);
          resolve();
        },
        () => resolve(),
      );
    });
    return;
  }
  if (!entry.isDirectory) return;
  try {
    const reader = entry.createReader();
    const entries = await _readAllWebkitDirectoryEntries(reader);
    for (const child of entries) {
      await _collectFromWebkitEntry(child, uniqueMap);
    }
  } catch {
    // Ignore directory traversal errors and continue with other items.
  }
}

export async function extractSupportedImportFilesFromDataTransfer(
  dataTransfer,
) {
  if (!dataTransfer) return [];

  const unique = new Map();
  const items = dataTransfer.items ? Array.from(dataTransfer.items) : [];

  // IMPORTANT: Safari clears the DataTransfer object after the first `await`,
  // so all synchronous DataTransfer access must complete before any async work.
  // webkitGetAsEntry() is the primary method — it is synchronous, handles
  // directories, and is supported in all modern browsers (Chrome, Edge,
  // Firefox, Safari). getAsFile() serves as a per-item fallback.
  const webkitEntries = [];
  const fallbackFiles = [];

  for (const item of items) {
    if (!item || item.kind !== "file") continue;

    if (typeof item.webkitGetAsEntry === "function") {
      try {
        const entry = item.webkitGetAsEntry();
        if (entry) {
          webkitEntries.push(entry);
          continue;
        }
      } catch {
        // Fall through to getAsFile().
      }
    }

    if (typeof item.getAsFile === "function") {
      fallbackFiles.push(item.getAsFile());
    }
  }

  // Capture dataTransfer.files synchronously before any awaits (final fallback
  // for browsers that expose no items list at all).
  const directFiles = Array.from(dataTransfer.files || []);

  // --- All synchronous DataTransfer access is done. Now we can safely await.
  // ---

  for (const entry of webkitEntries) {
    await _collectFromWebkitEntry(entry, unique);
  }

  for (const file of fallbackFiles) {
    _addIfSupportedFile(file, unique);
  }

  for (const file of directFiles) {
    _addIfSupportedFile(file, unique);
  }

  return Array.from(unique.values());
}

export function MediaFormat(source) {
  if (!source) return "";
  if (typeof source === "string") {
    const trimmed = source.trim().toLowerCase();
    if (!trimmed) return "";
    const stripped = trimmed.split("?")[0].split("#")[0];
    if (!stripped) return "";
    const parts = stripped.split(".");
    return parts.length > 1 ? parts.pop() : stripped;
  }
  if (source.format) return MediaFormat(source.format);
  if (source.filename) return MediaFormat(source.filename);
  if (source.url) return MediaFormat(source.url);
  return "";
}

// Map a media extension (or a source with a derivable format) to a MIME type.
// Used for the `DownloadURL` drag-out hint so the OS file manager creates a
// correctly-typed file. Unknown formats fall back to a generic binary type,
// which still downloads correctly.
const MEDIA_MIME_TYPES = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  gif: "image/gif",
  webp: "image/webp",
  bmp: "image/bmp",
  tif: "image/tiff",
  tiff: "image/tiff",
  svg: "image/svg+xml",
  ico: "image/x-icon",
  cur: "image/x-icon",
  avif: "image/avif",
  heic: "image/heic",
  heif: "image/heif",
  mp4: "video/mp4",
  m4v: "video/mp4",
  webm: "video/webm",
  mov: "video/quicktime",
  avi: "video/x-msvideo",
  mkv: "video/x-matroska",
  flv: "video/x-flv",
  wmv: "video/x-ms-wmv",
};

export function mediaMimeType(source) {
  const ext = MediaFormat(source);
  if (!ext) return "application/octet-stream";
  return MEDIA_MIME_TYPES[ext] || "application/octet-stream";
}

// Sanitise a user-supplied name for use as a drag-out download filename. The
// `DownloadURL` dataTransfer hint is "<mime>:<filename>:<url>", so a name that
// carries path separators, a colon, or a newline can redirect or break the
// saved file. This matters in the Electron shell, which sanitises drag-out
// names less than a plain browser download does. Takes the basename (drops
// everything up to the last / or \), replaces control chars and ':' with '_',
// trims, and falls back to `fallback` when nothing usable remains.
export function safeDownloadName(name, fallback = "download") {
  const raw = typeof name === "string" ? name : "";
  // Basename: strip everything up to and including the last / or \.
  const slashIdx = Math.max(raw.lastIndexOf("/"), raw.lastIndexOf("\\"));
  const base = slashIdx >= 0 ? raw.slice(slashIdx + 1) : raw;
  // Replace control chars (newlines, tabs, etc.) and the ':' separator with '_'.
  // eslint-disable-next-line no-control-regex
  const cleaned = base.replace(/[\u0000-\u001f\u007f:]/g, "_").trim();
  if (!cleaned) return fallback;
  // Cap at 255 chars (the common filesystem filename limit) so a pathological
  // multi-KB original_file_name can't produce an unbounded download name.
  // Keep a short real extension; otherwise hard-truncate.
  const MAX = 255;
  if (cleaned.length <= MAX) return cleaned;
  const dot = cleaned.lastIndexOf(".");
  const ext = dot > 0 && cleaned.length - dot <= 16 ? cleaned.slice(dot) : "";
  return cleaned.slice(0, MAX - ext.length) + ext;
}

export function getPictureId(id) {
  if (id === null || id === undefined) return null;
  return String(id);
}

export function buildMediaUrl({ backendUrl, image, format } = {}) {
  if (!backendUrl || !image || !image.id) return "";
  const ext = MediaFormat(format || image);
  // The extension selects the native-media route. Without it this URL points
  // at the JSON picture-detail resource, whose 200 response cannot be decoded
  // by <img>/<video> elements.
  if (!ext) return "";
  const cacheBuster = image.pixel_sha ? `?v=${image.pixel_sha}` : "";
  return `${backendUrl}/pictures/${image.id}.${ext}${cacheBuster}`;
}

export function getOverlayFormat(overlayImage) {
  return MediaFormat(overlayImage) || "png";
}

export function isFileDrag(dataTransfer) {
  if (!dataTransfer) return false;
  const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
  return types.includes("Files") || types.includes("application/x-moz-file");
}

/**
 * True when a drag originates inside the app (grid thumbnails dragged onto a
 * character / set / project), identified by our own `application/json` payload.
 *
 * This must be distinguished from an external OS file drop because the desktop
 * shell (Electron) populates `dataTransfer.files` with the dragged in-page image
 * as a real File — which the web does not — so a `files.length > 0` check alone
 * misreads an internal assign-drag as a file import. Only `types` is readable
 * during `dragover` (the payload itself is protected until `drop`), so key off
 * the type list, the same signal the drop handlers use.
 */
export function isInternalImageDrag(dataTransfer) {
  if (!dataTransfer) return false;
  const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
  return types.includes("application/json");
}

/**
 * Marker types that carry the *kind* of an internal drag payload.
 *
 * Every internal payload travels as `application/json`, whose body is protected
 * during `dragover` (getData() returns "" in Chrome and Firefox); only `types`
 * is readable. The discriminator therefore has to be the key, not a field in
 * the body, or a drop target cannot tell a picture drag from a face drag until
 * the drop has already happened.
 */
export const PICTURE_DRAG_MIME = "application/x-pixlstash-pictures";
export const FACE_DRAG_MIME = "application/x-pixlstash-faces";
/**
 * Registered copies of model files, dragged from the shelf onto a folder.
 *
 * A third marker rather than a field in the body, for the reason above and for
 * one more: the sidebar's set and character rows accept pictures, and a model
 * dropped on one has no meaning at all. `types` is what refuses it during
 * dragover, before the pointer ever suggests the drop would work.
 */
export const MODEL_FILE_DRAG_MIME = "application/x-pixlstash-model-files";

/**
 * Payload `type` to its marker. A kind absent from this map gets no marker, so
 * no drop target accepts it — an unmapped payload must fail closed rather than
 * inherit the picture marker and be filed as a picture drag (issue #757 again,
 * one payload kind later).
 */
const DRAG_MARKERS = {
  "image-ids": PICTURE_DRAG_MIME,
  "face-bbox": FACE_DRAG_MIME,
  "model-files": MODEL_FILE_DRAG_MIME,
};

/**
 * Write an internal drag payload: the JSON body every drop handler reads, plus
 * the marker type its kind is recognised by during dragover.
 */
export function setInternalDragPayload(dataTransfer, payload) {
  if (!dataTransfer || !payload) return;
  dataTransfer.setData("application/json", JSON.stringify(payload));
  const marker = DRAG_MARKERS[payload.type];
  if (!marker) {
    console.error(
      `Internal drag payload type "${payload.type}" has no marker in ` +
        "DRAG_MARKERS, so no drop target will accept it. Add one.",
    );
    return;
  }
  dataTransfer.setData(marker, payload.type);
}

/** True when the drag carries pictures (grid thumbnails, the open overlay). */
export function isPictureDrag(dataTransfer) {
  if (!dataTransfer) return false;
  const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
  return types.includes(PICTURE_DRAG_MIME);
}

/** True when the drag carries registered copies of model files. */
export function isModelFileDrag(dataTransfer) {
  if (!dataTransfer) return false;
  const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
  return types.includes(MODEL_FILE_DRAG_MIME);
}

/** True when the drag carries face bounding boxes. */
export function isFaceDrag(dataTransfer) {
  if (!dataTransfer) return false;
  const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
  return types.includes(FACE_DRAG_MIME);
}

export function isVideo(img) {
  if (!img) return false;
  const format = MediaFormat(img);
  if (format) {
    return isSupportedVideoFile(`file.${format}`);
  }
  return isSupportedVideoFile(img.id || "");
}
