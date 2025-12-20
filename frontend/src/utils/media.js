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

export function isSupportedImageFile(file) {
  const ext = (file.name || file).split('.').pop().toLowerCase();
  return PIL_IMAGE_EXTENSIONS.includes(ext);
}

export function isSupportedVideoFile(file) {
  const filename = typeof file === 'string' ? file : file.name || '';

  const ext = filename.split('.').pop().toLowerCase();
  return VIDEO_EXTENSIONS.includes(ext);
}

export function isSupportedMediaFile(file) {
  return isSupportedImageFile(file) || isSupportedVideoFile(file);
}

export function dataTransferHasSupportedMedia(dataTransfer) {
  if (!dataTransfer) return false;
  const items = dataTransfer.items ? Array.from(dataTransfer.items) : [];
  for (let i = 0; i < Math.min(items.length, 10); i++) {
    const item = items[i];
    if (!item || item.kind !== 'file') continue;
    const mime = item.type || '';
    if (typeof mime === 'string' && (mime.startsWith('image/') || mime.startsWith('video/'))) {
      return true;
    }
    if (!mime && typeof item.getAsFile === 'function') {
      const file = item.getAsFile();
      if (file && isSupportedMediaFile(file)) {
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

export function getOverlayFormat(overlayImage) {
  if (!overlayImage) return '';
  if (overlayImage.format) return overlayImage.format;
  if (overlayImage.filename) {
    return overlayImage.filename.split('.').pop().toLowerCase();
  }
  if (overlayImage.url) {
    return overlayImage.url.split('.').pop().toLowerCase();
  }
  if (overlayImage.id) {
    return overlayImage.id.split('.').pop().toLowerCase();
  }
  return 'png';
}