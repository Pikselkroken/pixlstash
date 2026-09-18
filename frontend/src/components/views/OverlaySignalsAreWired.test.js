// Every overlay-only WS signal the store exposes must actually be bound to the
// lightbox.
//
// These signals exist because the grid's own refresh is deferred under an open
// overlay (§9.1), so the overlay has to hear about the change itself. The chain
// is four links - backend field, `useUpdatesSocket` emitter, the store ref, the
// `ImageGrid` -> `ImageOverlay` prop binding - and the last one is a line of
// template. Nothing that mounts ImageOverlay and sets the prop by hand can see
// it missing, which is how #1419's fix was first written with two of its four
// links deletable and the whole suite still green.
//
// Asserted against the SOURCE because a template binding is source: mounting
// ImageGrid to check one attribute would cost more than the bug does.

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const read = (relative) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");

const gridSource = read("./ImageGrid.vue");
const storeSource = read("../../stores/useWsStore.js");
const overlaySource = read("./ImageOverlay.vue");

// Every `ws…Update` the store exposes. `wsTagUpdate` belongs here too: the grid
// consumes it (the LIKENESS_GROUPS re-rank, the tag-filter refresh) AND
// ImageGrid binds it to the overlay, whose watcher re-reads metadata on a
// foreign tag edit - so the template line is load-bearing for the lightbox and
// deleting it is exactly the outage this file exists to catch. An earlier
// version of this list excluded it on the opposite, wrong premise, which left
// the one signal it skipped unguarded.
//
// `wsPluginProgress` is not in this family: it reaches the overlay through
// App.vue's own `pluginProgress` prop, not as a `ws…Update` signal.
const OVERLAY_SIGNALS = [
  ["wsTagUpdate", "tagUpdate"],
  ["wsDescriptionUpdate", "descriptionUpdate"],
  ["wsSmartScoreUpdate", "smartScoreUpdate"],
  ["wsDetectionUpdate", "detectionUpdate"],
  ["wsTextUpdate", "textUpdate"],
  ["wsOrientationUpdate", "orientationUpdate"],
];

describe("overlay WS signals are wired end to end", () => {
  it.each(OVERLAY_SIGNALS)(
    "%s is exported by the store, bound by ImageGrid and declared by ImageOverlay",
    (storeRef, propName) => {
      // The store exposes it (a ref declared but left out of the return is the
      // same outage as no ref at all).
      expect(storeSource).toContain(`const ${storeRef} = ref(`);
      expect(storeSource).toMatch(
        new RegExp(`^\\s*${storeRef},\\s*$`, "m"),
        `useWsStore does not return ${storeRef}`,
      );
      // ImageGrid binds it onto the overlay.
      expect(gridSource).toContain(`:${propName}="wsStore.${storeRef}"`);
      // ImageOverlay declares the prop it is bound to.
      expect(overlaySource).toMatch(
        new RegExp(`^\\s*${propName}:\\s*\\{\\s*type:`, "m"),
        `ImageOverlay has no \`${propName}\` prop`,
      );
    },
  );

  it("names every signal the store actually exposes", () => {
    // The point of the list above is to be complete. A new `ws*Update` ref that
    // nobody added here would slip through the per-signal checks entirely, so
    // catch it by counting - with no exclusion list, because an exclusion is
    // how the last hole got in.
    const declared = [
      ...storeSource.matchAll(/const (ws\w*Update) = ref\(/g),
    ].map((m) => m[1]);
    expect(declared.sort()).toEqual(OVERLAY_SIGNALS.map(([r]) => r).sort());
  });
});
