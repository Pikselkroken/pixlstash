// media.js - Shared media/file helpers for PixlStash frontend

export const PIL_IMAGE_EXTENSIONS = [
  'jpg',  'jpeg', 'png', 'bmp',  'gif', 'tiff', 'tif',  'webp', 'ppm', 'pgm',
  'pbm',  'pnm',  'ico', 'icns', 'svg', 'dds',  'msp',  'pcx',  'xbm', 'im',
  'fli',  'flc',  'eps', 'psd',  'pdf', 'jp2',  'j2k',  'jpf',  'jpx', 'j2c',
  'jpc',  'tga',  'ras', 'sgi',  'rgb', 'rgba', 'bw',   'exr',  'hdr', 'pic',
  'pict', 'pct',  'cur', 'emf',  'wmf', 'heic', 'heif', 'avif'
];

export const VIDEO_EXTENSIONS =
    ['mp4', 'avi', 'mov', 'webm', 'mkv', 'flv', 'wmv', 'm4v'];

export const ARCHIVE_EXTENSIONS = ['zip'];

export const CAPTION_EXTENSIONS = ['txt'];

export function isSupportedImageFile(file) {
  const ext = (file.name || file).split('.').pop().toLowerCase();
  return PIL_IMAGE_EXTENSIONS.includes(ext);
}

export function isSupportedVideoFile(file) {
  const filename = typeof file === 'string' ? file : file.name || '';

  const ext = filename.split('.').pop().toLowerCase();
  return VIDEO_EXTENSIONS.includes(ext);
}

export function isSupportedArchiveFile(file) {
  const filename = typeof file === 'string' ? file : file.name || '';
  const ext = filename.split('.').pop().toLowerCase();
  return ARCHIVE_EXTENSIONS.includes(ext);
}

export function isSupportedMediaFile(file) {
  return isSupportedImageFile(file) || isSupportedVideoFile(file);
}

export function isSupportedCaptionFile(file) {
  const ext = (typeof file === 'string' ? file : file?.name || '').split('.').pop().toLowerCase();
  return CAPTION_EXTENSIONS.includes(ext);
}

export function isSupportedImportFile(file) {
  return isSupportedMediaFile(file) || isSupportedArchiveFile(file) || isSupportedCaptionFile(file);
}

function _fileDedupKey(file) {
  const name = file?.name || '';
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

export async function extractSupportedImportFilesFromDataTransfer(dataTransfer) {
  if (!dataTransfer) return [];

  const unique = new Map();
  const items = dataTransfer.items ? Array.from(dataTransfer.items) : [];

  // IMPORTANT: Safari clears the DataTransfer object after the first `await`,
  // so all synchronous DataTransfer access must complete before any async work.
  // webkitGetAsEntry() is the primary method — it is synchronous, handles
  // directories, and is supported in all modern browsers (Chrome, Edge, Firefox,
  // Safari). getAsFile() serves as a per-item fallback.
  const webkitEntries = [];
  const fallbackFiles = [];

  for (const item of items) {
    if (!item || item.kind !== 'file') continue;

    if (typeof item.webkitGetAsEntry === 'function') {
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

    if (typeof item.getAsFile === 'function') {
      fallbackFiles.push(item.getAsFile());
    }
  }

  // Capture dataTransfer.files synchronously before any awaits (final fallback
  // for browsers that expose no items list at all).
  const directFiles = Array.from(dataTransfer.files || []);

  // --- All synchronous DataTransfer access is done. Now we can safely await. ---

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

export function dataTransferHasSupportedMedia(dataTransfer) {
  if (!dataTransfer) return false;
  const items = dataTransfer.items ? Array.from(dataTransfer.items) : [];
  for (let i = 0; i < Math.min(items.length, 10); i++) {
    const item = items[i];
    if (!item || item.kind !== 'file') continue;
    const mime = item.type || '';
    if (typeof mime === 'string' &&
        (mime.startsWith('image/') || mime.startsWith('video/') ||
         mime === 'application/zip' ||
         mime === 'application/x-zip-compressed')) {
      return true;
    }
    if (!mime && typeof item.getAsFile === 'function') {
      const file = item.getAsFile();
      if (file && isSupportedImportFile(file)) {
        return true;
      }
    }
  }
  if (items.length === 0) {
    const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
    if (types.includes('Files')) {
      return true;
    }
  }
  return false;
}

export function MediaFormat(source) {
  if (!source) return '';
  if (typeof source === 'string') {
    const trimmed = source.trim().toLowerCase();
    if (!trimmed) return '';
    const stripped = trimmed.split('?')[0].split('#')[0];
    if (!stripped) return '';
    const parts = stripped.split('.');
    return parts.length > 1 ? parts.pop() : stripped;
  }
  if (source.format) return MediaFormat(source.format);
  if (source.filename) return MediaFormat(source.filename);
  if (source.url) return MediaFormat(source.url);
  if (source.id) return MediaFormat(source.id);
  return '';
}

export function getPictureId(id) {
  if (id === null || id === undefined) return null;
  return String(id);
}

export function buildMediaUrl({backendUrl, image, format} = {}) {
  if (!backendUrl || !image || !image.id) return '';
  const ext = MediaFormat(format || image);
  const suffix = ext ? `.${ext}` : '';
  const cacheBuster = image.pixel_sha ? `?v=${image.pixel_sha}` : '';
  const url = `${backendUrl}/pictures/${image.id}${suffix}${cacheBuster}`;
  return url;
}

export function getOverlayFormat(overlayImage) {
  return MediaFormat(overlayImage) || 'png';
}

export function isFileDrag(dataTransfer) {
  if (!dataTransfer) return false;
  const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
  return types.includes('Files') || types.includes('application/x-moz-file');
}

export function isVideo(img) {
  if (!img) return false;
  const format = MediaFormat(img);
  if (format) {
    return isSupportedVideoFile(`file.${format}`);
  }
  return isSupportedVideoFile(img.id || '');
}