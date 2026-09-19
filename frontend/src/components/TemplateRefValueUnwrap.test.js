// A template never writes `.value` on a setup ref.
//
// Inside `<template>`, a ref declared in `<script setup>` is auto-unwrapped and
// the SFC compiler supplies the `.value` itself, so `someRef.value = x` in a
// template compiles to `someRef.value.value = x`. Module code is strict, so it
// throws - `Cannot set properties of null` when the ref holds null, or
// `Cannot create property 'value' on number` when it holds a primitive. Vue
// catches it in callWithErrorHandling, so nothing crashes: the handler just
// silently does nothing and a warning goes to the console.
//
// It has shipped twice (BehaviourSection.vue in #1253, ImageGrid.vue's
// @update:overlayImageId handler in #1256), it looks correct to anyone used to
// writing `.value` in script, and no linter here flags it. So the invariant is
// asserted against the compiler's own output over the whole component tree
// rather than by eye, per component.
//
// What this does NOT catch, so that nobody reads a green run as more than it
// is: a ref reached through anything but its own bare name compiles to
// `_unref(x).value = …` or `obj.x.value = …` and is invisible here - a
// destructured composable return, a ref held in a plain object or a
// `reactive()`, a parenthesised or bracket-indexed target. Only the bare-name
// shape, which is the one that has actually shipped, is guarded.

import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";

// vitest runs from the frontend/ package root.
const SRC = join(process.cwd(), "src");

// A `.value` WRITE, which is unambiguous. Reads are deliberately not flagged:
// `{{ opt.value }}` is correct and common against a `ref({ value, label })`
// option object, and the compiled form is identical to the broken one.
const VALUE_WRITE = /\.value\.value\s*=[^=]/;

// Components with no `<script setup>` or no `<template>` have nothing to check.
// Listed rather than silently dropped, so the walk cannot quietly stop covering
// a file that grows a template later.
const NOTHING_TO_CHECK = [
  "/components/settings/SettingsTwoCol.vue",
  "/components/widgets/FieldLabel.vue",
];

function vueFiles(dir) {
  const found = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) found.push(...vueFiles(path));
    else if (name.endsWith(".vue")) found.push(path);
  }
  return found;
}

/**
 * The component's compiled render function, on its own.
 *
 * Only the template is compiled, not the script body: writing `.value.value` in
 * `<script setup>` is normal and correct when the outer `.value` is a template
 * ref holding an `<input>` (AccountSection.vue and ModelShelf.vue both clear a
 * file input that way).
 */
function compiledTemplate(path) {
  const { descriptor } = parse(readFileSync(path, "utf8"), { filename: path });
  if (!descriptor.scriptSetup || !descriptor.template) return null;
  const { bindings } = compileScript(descriptor, { id: path });
  return compileTemplate({
    source: descriptor.template.content,
    filename: path,
    id: path,
    // `inline` is the form a production build of `<script setup>` emits, where
    // a setup ref is referenced directly and the compiler appends the `.value`
    // itself. Without it the render function goes through the `$setup` proxy,
    // which unwraps at runtime and leaves nothing in the generated code to see.
    compilerOptions: {
      bindingMetadata: bindings,
      inline: true,
      prefixIdentifiers: true,
    },
  }).code;
}

describe("no component writes .value on an auto-unwrapped ref", () => {
  const components = vueFiles(SRC).map((path) => ({
    path: path.slice(SRC.length),
    code: compiledTemplate(path),
  }));

  it("compiles every component it does not explicitly skip", () => {
    // A floor would let the walk lose most of the tree and still pass, so the
    // skipped set is named exactly instead.
    expect(
      components
        .filter((c) => c.code === null)
        .map((c) => c.path)
        .sort(),
    ).toEqual([...NOTHING_TO_CHECK].sort());
    expect(components.length).toBeGreaterThan(100);
  });

  it("compiles no `.value.value =`", () => {
    // ImageGrid.vue's @update:overlayImageId handler shipped one of these and
    // the overlay stopped following a ComfyUI run to its new picture.
    const offenders = components
      .filter((c) => c.code && VALUE_WRITE.test(c.code))
      .map((c) => c.path);

    expect(offenders).toEqual([]);
  });
});
