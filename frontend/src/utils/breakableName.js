/**
 * Split a name into words (at whitespace) and each word into chunks (at
 * camelCase humps and after `_`, `-`, `.`), so a long file-like name can wrap
 * at the joins rather than scroll its container sideways. A chunk that still
 * does not fit is the renderer's to ellipsize.
 *
 * "moodyKrea2Mix_v1 Upscale" -> [["moody", "Krea2", "Mix_", "v1"], ["Upscale"]]
 */
export function breakableName(name) {
  return String(name ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .map((word) =>
      word
        .split(/(?<=[_\-.])|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])/)
        .filter(Boolean),
    );
}
