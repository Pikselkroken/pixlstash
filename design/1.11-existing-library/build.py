#!/usr/bin/env python3
"""Assemble the .dc.html artboards from bodies/ + the shared design kit.

The kit is inlined into every artboard because a .dc.html cannot link a
stylesheet: the canvas runtime serves each artboard in its own sandboxed
iframe with no egress. `_kit.css` is a copy of the design system's
tokens/colors.css + ui_kits/app/unified.css and follows it, never the
other way round.

`_extra.css` holds only what this bundle adds on top of the kit: the
entity vocabulary (icon/colour per Project, Set, Character, Tag) and the
mapping surfaces, which have no counterpart in the shipped app yet.

    python3 build.py
"""

import pathlib

HERE = pathlib.Path(__file__).parent
KIT = (HERE / "_kit.css").read_text()
EXTRA = (HERE / "_extra.css").read_text()

TEMPLATE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <style>
{kit}
{extra}
  </style>
</helmet>
{body}
</x-dc>
<script data-dc-script data-props='{{}}'>
class Component extends DCLogic {{ renderVals() {{ return {{}}; }} }}
</script>
</body>
</html>
"""


def main() -> None:
    bodies = sorted((HERE / "bodies").glob("*.html"))
    if not bodies:
        raise SystemExit("no bodies/*.html to build")
    for body_path in bodies:
        out = HERE / f"{body_path.stem}.dc.html"
        out.write_text(
            TEMPLATE.format(kit=KIT, extra=EXTRA, body=body_path.read_text().rstrip())
        )
        print(f"{out.name}  {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
