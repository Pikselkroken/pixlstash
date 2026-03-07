// media.js - Shared media/file helpers for Pixelurgy Vault frontend

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

export function isSupportedImportFile(file) {
  return isSupportedMediaFile(file) || isSupportedArchiveFile(file);
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
  return `${backendUrl}/pictures/${image.id}${suffix}${cacheBuster}`;
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