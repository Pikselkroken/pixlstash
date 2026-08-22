# Linux icon set

`linux.icon` in `electron/package.json` points at this directory, not at
`assets/icon.png`. electron-builder only generates a size set from an `.icns`
or from a directory of `NxN.png` files: handed a single PNG it installs that
one file at its own resolution, which put our 1024² icon in
`hicolor/1024x1024/apps/`. `hicolor/index.theme` does not declare a
`1024x1024` directory, so no icon-theme lookup ever resolved
`pixlstash-desktop` and the desktop entry rendered with no icon at all (blank
in alt-tab, the window list and the menu).

Every size here is one `hicolor` declares. Regenerate them after any change to
`assets/icon.png` (a brand retone, for instance):

```
python - <<'PY'
from PIL import Image
src = Image.open('assets/icon.png').convert('RGBA')
for n in (16, 24, 32, 48, 64, 128, 256, 512):
    src.resize((n, n), Image.LANCZOS).save(f'assets/icons/{n}x{n}.png', optimize=True)
PY
```

`test/linuxIcons.test.ts` fails if a size goes missing or stops being square.
