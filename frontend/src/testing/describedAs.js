/**
 * An element's accessible description as a screen reader assembles it from
 * `aria-describedby`: each id, resolved to its node's text, joined. The nodes
 * may be hidden (`Tooltip`'s are); that does not stop them describing.
 */
export function describedAs(el) {
  const node = el?.element ?? el;
  return (node.getAttribute("aria-describedby") || "")
    .split(/\s+/)
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? "")
    .join(" ");
}
