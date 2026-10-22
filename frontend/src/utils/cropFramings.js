// The Edit tab's crop framings: boxes worked out from the picture's size and
// the face the library already stores, before anything is cut.
//
// Every box is `[left, top, width, height]` as fractions of the picture, the
// shape the built-in `crop` plugin takes, so a framing is the same small JSON
// whichever picture it lands on.

// SDXL's training buckets, the sizes trainers resize a dataset into.
export const TRAINING_BUCKETS = [
  [1024, 1024],
  [1152, 896],
  [896, 1152],
  [1216, 832],
  [832, 1216],
  [1344, 768],
  [768, 1344],
  [1536, 640],
  [640, 1536],
];

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

/**
 * The largest box of `aspect` (width / height) inside a `width x height`
 * picture, centred on `centre` (pixels) as far as the edges allow.
 */
function largestBox(width, height, aspect, centre) {
  let boxW = width;
  let boxH = width / aspect;
  if (boxH > height) {
    boxH = height;
    boxW = height * aspect;
  }
  const left = clamp(centre.x - boxW / 2, 0, width - boxW);
  const top = clamp(centre.y - boxH / 2, 0, height - boxH);
  return [left / width, top / height, boxW / width, boxH / height];
}

function faceCentre(face) {
  const [x1, y1, x2, y2] = face;
  return { x: (x1 + x2) / 2, y: (y1 + y2) / 2 };
}

/** The bucket whose shape is nearest the picture's. */
export function nearestBucket(width, height) {
  const aspect = width / height;
  return TRAINING_BUCKETS.reduce((best, bucket) =>
    Math.abs(Math.log(bucket[0] / bucket[1] / aspect)) <
    Math.abs(Math.log(best[0] / best[1] / aspect))
      ? bucket
      : best,
  );
}

/**
 * The framings to offer for one picture: `[{id, label, box}]`.
 *
 * `face` is a stored face box `[x1, y1, x2, y2]` in the picture's pixels, or
 * null. Square on face sizes the square to the face (two and a half face
 * heights, so the head and shoulders are in) and is offered only with a face;
 * 3:4 and the training bucket centre on the face when there is one, else on
 * the picture. Their face is upper-centre: a portrait's face sits above the
 * middle, so the box is centred a face height below it.
 */
export function cropFramings(width, height, face = null) {
  if (!(width > 0 && height > 0)) return [];
  const centre = face
    ? {
        x: faceCentre(face).x,
        y: faceCentre(face).y + (face[3] - face[1]),
      }
    : { x: width / 2, y: height / 2 };
  const framings = [];
  if (face) {
    const faceSize = Math.max(face[2] - face[0], face[3] - face[1]);
    const side = Math.min(faceSize * 2.5, width, height);
    const { x, y } = faceCentre(face);
    const left = clamp(x - side / 2, 0, width - side);
    const top = clamp(y - side / 2, 0, height - side);
    framings.push({
      id: "face",
      label: "Square on face",
      icon: "mdi-face-recognition",
      box: [left / width, top / height, side / width, side / height],
    });
  }
  framings.push({
    id: "portrait",
    label: face ? "3:4 on face" : "3:4",
    icon: "mdi-crop-portrait",
    box: largestBox(width, height, 3 / 4, centre),
  });
  const [bucketW, bucketH] = nearestBucket(width, height);
  framings.push({
    id: "bucket",
    label: `Training bucket ${bucketW}×${bucketH}`,
    icon: "mdi-layers-outline",
    box: largestBox(width, height, bucketW / bucketH, centre),
  });
  return framings;
}
